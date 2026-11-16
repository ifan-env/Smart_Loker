/*
  Smart Parcel Locker - Firmware ESP32 (gabungan)

  Modul : solenoid (via MOSFET), modul reed switch, ultrasonic HC-SR04,
          sensor kunci solenoid (kabel putih-hijau), RFID RC522
  Board : ESP32 Dev Module, Serial 115200

  Perintah dari laptop (akhiri dengan Enter):
    UNLOCK        -> buka solenoid (default 2000 ms)
    UNLOCK 2500   -> buka solenoid 2500 ms (dibatasi 500-3000 ms)
    LOCK          -> matikan solenoid sekarang
    STATUS        -> tampilkan status semua sensor
    CAL           -> simpan jarak sekarang sebagai "loker kosong"
    LIGHT ON      -> nyalakan lampu dalam loker (mati otomatis setelah 2 menit)
    LIGHT OFF     -> matikan lampu
    PING          -> dibalas PONG

  Tombol fisik "Titip Paket" di GPIO 4 (ke GND, INPUT_PULLUP). Firmware hanya
  melapor EVT BUTTON PRESS; server yang memutuskan boleh membuka atau tidak.

  Event ke laptop (satu baris, diawali "EVT"):
    EVT DOOR CLOSED | EVT DOOR OPEN
    EVT LOCK LOCKED | EVT LOCK RELEASED
    EVT DIST <cm>
    EVT OBJECT PRESENT | EVT OBJECT EMPTY
    EVT RFID <UID>
    EVT SOLENOID ON <ms> | EVT SOLENOID OFF
    EVT BUTTON PRESS
    EVT ALERT DOOR_FORCED
*/

#include <SPI.h>
#include <MFRC522.h>

// ===================== PIN =====================
const uint8_t PIN_SOLENOID = 26;   // gate MOSFET (lewat resistor 220 ohm)
const uint8_t PIN_REED     = 27;   // DO modul reed switch
const uint8_t PIN_LOCK     = 32;   // kabel putih solenoid (hijau ke GND)
const uint8_t PIN_TRIG     = 25;   // HC-SR04 Trig
const uint8_t PIN_ECHO     = 33;   // HC-SR04 Echo (lewat pembagi tegangan)
const uint8_t PIN_RFID_SS  = 5;
const uint8_t PIN_RFID_RST = 22;
const uint8_t PIN_SCK      = 18;
const uint8_t PIN_MISO     = 19;
const uint8_t PIN_MOSI     = 23;
const uint8_t PIN_LIGHT    = 14;   // gate MOSFET kedua untuk lampu LED (lewat resistor 220 ohm)
const uint8_t PIN_BUTTON   = 4;    // tombol "Titip Paket" ke GND (INPUT_PULLUP)

// ===================== KONFIGURASI =====================
// Sesuaikan setelah tes: level pin saat kondisi tersebut terjadi
const int REED_CLOSED_LEVEL = LOW;    // level DO saat pintu tertutup (magnet dekat)
const int LOCK_LOCKED_LEVEL = LOW;    // level putih-hijau saat kait terkunci

const unsigned long SOLENOID_DEFAULT_MS = 2000;
const unsigned long SOLENOID_MIN_MS     = 500;
const unsigned long SOLENOID_MAX_MS     = 3000;   // batas aman, jangan dinaikkan
const unsigned long DOOR_GRACE_MS       = 3000;   // pintu boleh terbuka sesaat setelah unlock

const unsigned long DEBOUNCE_MS         = 50;
const unsigned long DIST_INTERVAL_MS    = 500;
const float DIST_REPORT_DELTA_CM        = 2.0;    // lapor jika jarak berubah >= ini
const float OBJECT_MARGIN_CM            = 3.0;    // dianggap ada benda jika lebih dekat dari baseline - margin
const unsigned long RFID_REPEAT_MS      = 1500;   // cegah baca kartu yang sama berulang
const unsigned long BUTTON_REPEAT_MS    = 1000;   // satu tekanan = satu event
const unsigned long LIGHT_MAX_MS        = 120000; // lampu mati otomatis setelah 2 menit (pengaman)

// ===================== DEBOUNCE =====================
struct Debounced {
  uint8_t pin;
  int stable;
  int lastRaw;
  unsigned long t;

  void begin(uint8_t p, uint8_t mode) {
    pin = p;
    pinMode(p, mode);
    lastRaw = digitalRead(p);
    stable = lastRaw;
    t = millis();
  }
  // true jika nilai stabil berubah
  bool update() {
    int raw = digitalRead(pin);
    if (raw != lastRaw) {
      lastRaw = raw;
      t = millis();
    }
    if (raw != stable && millis() - t >= DEBOUNCE_MS) {
      stable = raw;
      return true;
    }
    return false;
  }
};

Debounced reed;
Debounced lockSense;
Debounced tombol;

// ===================== STATE =====================
MFRC522 rfid(PIN_RFID_SS, PIN_RFID_RST);

bool solenoidOn = false;
unsigned long solenoidOffAt = 0;
unsigned long lastUnlockEnd = 0;
bool everUnlocked = false;

float lastDist = -1;
float lastReportedDist = -1;
float baselineCm = -1;
bool objectPresent = false;
unsigned long lastDistAt = 0;

String lastUid = "";
unsigned long lastUidAt = 0;

unsigned long lastButtonAt = 0;

bool lightOn = false;
unsigned long lightOffAt = 0;

String cmdBuf = "";

// ===================== HELPER =====================
bool doorClosed()  { return reed.stable == REED_CLOSED_LEVEL; }
bool lockLocked()  { return lockSense.stable == LOCK_LOCKED_LEVEL; }

void solenoidOpen(unsigned long ms) {
  if (ms < SOLENOID_MIN_MS) ms = SOLENOID_MIN_MS;
  if (ms > SOLENOID_MAX_MS) ms = SOLENOID_MAX_MS;
  digitalWrite(PIN_SOLENOID, HIGH);
  solenoidOn = true;
  solenoidOffAt = millis() + ms;
  everUnlocked = true;
  Serial.print("EVT SOLENOID ON ");
  Serial.println(ms);
}

void solenoidClose() {
  digitalWrite(PIN_SOLENOID, LOW);
  if (solenoidOn) {
    solenoidOn = false;
    lastUnlockEnd = millis();
    Serial.println("EVT SOLENOID OFF");
  }
}

void lightSet(bool on) {
  digitalWrite(PIN_LIGHT, on ? HIGH : LOW);
  if (on) lightOffAt = millis() + LIGHT_MAX_MS;
  if (on != lightOn) {
    lightOn = on;
    Serial.println(on ? "EVT LIGHT ON" : "EVT LIGHT OFF");
  }
}

// ===================== ULTRASONIC =====================
float readDistanceOnce() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long d = pulseIn(PIN_ECHO, HIGH, 25000);   // timeout 25 ms (~4 m)
  if (d == 0) return -1;
  return d * 0.0343f / 2.0f;
}

// median dari 3 pembacaan; -1 jika tidak ada pantulan
float readDistance() {
  float v[3];
  int n = 0;
  for (int i = 0; i < 3; i++) {
    float d = readDistanceOnce();
    if (d > 0) v[n++] = d;
    delay(20);
  }
  if (n == 0) return -1;
  for (int i = 0; i < n - 1; i++)
    for (int j = i + 1; j < n; j++)
      if (v[j] < v[i]) { float t = v[i]; v[i] = v[j]; v[j] = t; }
  return v[n / 2];
}

void updateObjectState(float d) {
  if (baselineCm < 0 || d < 0) return;
  bool now = objectPresent;
  if (!objectPresent && d < baselineCm - OBJECT_MARGIN_CM) now = true;
  else if (objectPresent && d > baselineCm - OBJECT_MARGIN_CM / 2) now = false;
  if (now != objectPresent) {
    objectPresent = now;
    Serial.println(objectPresent ? "EVT OBJECT PRESENT" : "EVT OBJECT EMPTY");
  }
}

void handleDistance() {
  if (millis() - lastDistAt < DIST_INTERVAL_MS) return;
  lastDistAt = millis();
  float d = readDistance();
  lastDist = d;
  updateObjectState(d);
  if (d > 0 && (lastReportedDist < 0 || fabs(d - lastReportedDist) >= DIST_REPORT_DELTA_CM)) {
    lastReportedDist = d;
    Serial.print("EVT DIST ");
    Serial.println(d, 1);
  }
}

// ===================== RFID =====================
void handleRFID() {
  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial()) return;

  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  if (uid != lastUid || millis() - lastUidAt > RFID_REPEAT_MS) {
    Serial.print("EVT RFID ");
    Serial.println(uid);
  }
  lastUid = uid;
  lastUidAt = millis();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

// ===================== PERINTAH SERIAL =====================
void printStatus() {
  Serial.print("STATUS door=");
  Serial.print(doorClosed() ? "CLOSED" : "OPEN");
  Serial.print(" lock=");
  Serial.print(lockLocked() ? "LOCKED" : "RELEASED");
  Serial.print(" solenoid=");
  Serial.print(solenoidOn ? "ON" : "OFF");
  Serial.print(" dist=");
  if (lastDist > 0) Serial.print(lastDist, 1); else Serial.print("NA");
  Serial.print(" baseline=");
  if (baselineCm > 0) Serial.print(baselineCm, 1); else Serial.print("NA");
  Serial.print(" object=");
  Serial.print(objectPresent ? "PRESENT" : "EMPTY");
  Serial.print(" light=");
  Serial.println(lightOn ? "ON" : "OFF");
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("UNLOCK")) {
    unsigned long ms = SOLENOID_DEFAULT_MS;
    if (cmd.length() > 6) {
      long v = cmd.substring(6).toInt();
      if (v > 0) ms = (unsigned long)v;
    }
    if (!lockLocked()) {
      Serial.println("INFO kait tidak terdeteksi, solenoid tetap diaktifkan");
    }
    solenoidOpen(ms);
  } else if (cmd == "LOCK") {
    solenoidClose();
  } else if (cmd == "STATUS") {
    printStatus();
  } else if (cmd == "CAL") {
    float d = readDistance();
    if (d > 0) {
      baselineCm = d;
      objectPresent = false;
      Serial.print("OK CAL baseline=");
      Serial.println(baselineCm, 1);
    } else {
      Serial.println("ERR CAL tidak ada pantulan");
    }
  } else if (cmd == "LIGHT ON") {
    lightSet(true);
  } else if (cmd == "LIGHT OFF") {
    lightSet(false);
  } else if (cmd == "PING") {
    Serial.println("PONG");
  } else {
    Serial.print("ERR perintah tidak dikenal: ");
    Serial.println(cmd);
  }
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdBuf.length() > 0) {
        handleCommand(cmdBuf);
        cmdBuf = "";
      }
    } else if (cmdBuf.length() < 40) {
      cmdBuf += c;
    }
  }
}

// ===================== SENSOR DISKRET =====================
void handleDiscreteSensors() {
  if (reed.update()) {
    Serial.println(doorClosed() ? "EVT DOOR CLOSED" : "EVT DOOR OPEN");
    // pintu terbuka tanpa perintah sah
    if (!doorClosed() && !solenoidOn &&
        (!everUnlocked || millis() - lastUnlockEnd > DOOR_GRACE_MS)) {
      Serial.println("EVT ALERT DOOR_FORCED");
    }
  }
  if (lockSense.update()) {
    Serial.println(lockLocked() ? "EVT LOCK LOCKED" : "EVT LOCK RELEASED");
    // kait lepas tanpa perintah sah (dibuka paksa)
    if (!lockLocked() && !solenoidOn &&
        (!everUnlocked || millis() - lastUnlockEnd > DOOR_GRACE_MS)) {
      Serial.println("EVT ALERT LOCK_FORCED");
    }
  }
}

// ===================== TOMBOL TITIP PAKET =====================
void handleButton() {
  if (!tombol.update() || tombol.stable != LOW) return;
  if (millis() - lastButtonAt < BUTTON_REPEAT_MS) return;
  lastButtonAt = millis();
  Serial.println("EVT BUTTON PRESS");
}

// ===================== SETUP & LOOP =====================
void setup() {
  Serial.begin(115200);

  pinMode(PIN_SOLENOID, OUTPUT);
  digitalWrite(PIN_SOLENOID, LOW);        // pastikan terkunci saat boot

  pinMode(PIN_LIGHT, OUTPUT);
  digitalWrite(PIN_LIGHT, LOW);           // lampu mati saat boot

  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);

  reed.begin(PIN_REED, INPUT);            // modul reed punya penguat sendiri
  lockSense.begin(PIN_LOCK, INPUT_PULLUP);
  tombol.begin(PIN_BUTTON, INPUT_PULLUP); // ditekan = LOW

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_RFID_SS);
  rfid.PCD_Init();
  delay(50);
  byte ver = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.print("RFID versi: 0x");
  Serial.println(ver, HEX);
  if (ver == 0x00 || ver == 0xFF) {
    Serial.println("PERINGATAN: RC522 tidak terdeteksi, cek kabel SPI dan daya 3V3");
  }

  Serial.println("Locker siap. Perintah: UNLOCK [ms], LOCK, STATUS, CAL, PING");
  printStatus();
}

void loop() {
  handleSerial();

  // auto-lock: matikan solenoid saat batas waktu habis
  if (solenoidOn && (long)(millis() - solenoidOffAt) >= 0) {
    solenoidClose();
  }

  // pengaman: lampu mati otomatis setelah batas waktu
  if (lightOn && (long)(millis() - lightOffAt) >= 0) {
    lightSet(false);
  }

  handleDiscreteSensors();
  handleButton();
  handleRFID();
  handleDistance();
}
