"""Jembatan serial ke ESP32.

Prinsip (lihat AGENTS.md bagian runtime):
  - SATU pemilik port serial: thread pembaca di file ini saja.
  - Route tidak pernah membuka port; mereka memanggil `send()`.
  - Parser dipisah (`urai_baris`) supaya bisa diuji tanpa hardware.

Mode `mock` menjalankan simulator protokol yang sama, jadi seluruh alur
state machine bisa diuji tanpa loker fisik.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

_EVT = {
    ("DOOR", "CLOSED"): ("DOOR", {"state": "closed"}),
    ("DOOR", "OPEN"): ("DOOR", {"state": "open"}),
    ("LOCK", "LOCKED"): ("LOCK", {"state": "locked"}),
    ("LOCK", "RELEASED"): ("LOCK", {"state": "released"}),
    ("OBJECT", "PRESENT"): ("OBJECT", {"state": "present"}),
    ("OBJECT", "EMPTY"): ("OBJECT", {"state": "empty"}),
    ("LIGHT", "ON"): ("LIGHT", {"state": "on"}),
    ("LIGHT", "OFF"): ("LIGHT", {"state": "off"}),
    ("SOLENOID", "OFF"): ("SOLENOID", {"state": "off"}),
    ("BUTTON", "PRESS"): ("BUTTON", {"state": "press"}),
}

_SENSOR_AWAL = {
    "door": "open",
    "lock": "released",
    "solenoid": "off",
    "dist": None,
    "baseline": None,
    "object": "empty",
    "light": "off",
}


def urai_baris(baris: str):
    """Ubah satu baris protokol menjadi (jenis, payload).

    jenis: EVT | STATUS | OK | ERR | PONG | INFO | None (tak dikenal)
    """
    baris = (baris or "").strip()
    if not baris:
        return None, None

    if baris == "PONG":
        return "PONG", {}
    if baris.startswith("OK"):
        return "OK", {"teks": baris[2:].strip()}
    if baris.startswith("ERR"):
        return "ERR", {"teks": baris[3:].strip()}
    if baris.startswith("INFO"):
        return "INFO", {"teks": baris[4:].strip()}
    if baris.startswith("STATUS"):
        hasil = {}
        for bagian in baris.split()[1:]:
            if "=" in bagian:
                k, v = bagian.split("=", 1)
                hasil[k] = v
        return "STATUS", hasil

    if not baris.startswith("EVT"):
        return None, None

    sisa = baris[3:].strip()
    if not sisa:
        return None, None

    # Event dua kata yang dikenal (DOOR CLOSED, LOCK LOCKED, ...)
    for (kata1, kata2), (jenis, payload) in _EVT.items():
        if sisa.startswith(f"{kata1} {kata2}"):
            return jenis, dict(payload)

    if sisa.startswith("DIST"):
        try:
            return "DIST", {"cm": float(sisa.split()[1])}
        except (IndexError, ValueError):
            return "DIST", {"cm": None}
    if sisa.startswith("RFID"):
        bagian = sisa.split()
        uid = bagian[1].upper() if len(bagian) > 1 else ""
        return ("RFID", {"uid": uid}) if uid else (None, None)
    if sisa.startswith("SOLENOID ON"):
        try:
            ms = int(sisa.split()[2])
        except (IndexError, ValueError):
            ms = None
        return "SOLENOID", {"state": "on", "ms": ms}
    if sisa.startswith("ALERT"):
        bagian = sisa.split()
        return "ALERT", {"kind": bagian[1] if len(bagian) > 1 else "UNKNOWN"}

    return "EVT_TAK_DIKENAL", {"mentah": baris}


class _MockLink:
    """Simulator ESP32: meniru protokol, termasuk urutan event sungguhan."""

    def __init__(self, baud=115200):
        self.sensor = dict(_SENSOR_AWAL)
        self.sensor["dist"] = 30.0
        self.sensor["baseline"] = 30.0
        self._keluar = deque()       # (siap_pada, bytes)
        self._mulai = time.monotonic()
        self._hint = None
        self.terbuka = True

    # ---------- antrean keluaran ----------
    def _t(self):
        return time.monotonic() - self._mulai

    def _antri(self, baris, tunda=0.0):
        self._keluar.append((self._t() + tunda, (baris + "\n").encode()))

    def readline(self):
        sekarang = self._t()
        while self._keluar and self._keluar[0][0] <= sekarang:
            return self._keluar.popleft()[1]
        return b""

    def _status(self):
        s = self.sensor
        dist = f"{s['dist']:.1f}" if s["dist"] is not None else "NA"
        base = f"{s['baseline']:.1f}" if s["baseline"] is not None else "NA"
        self._antri(
            f"STATUS door={s['door'].upper()} lock={s['lock'].upper()} "
            f"solenoid={s['solenoid'].upper()} dist={dist} baseline={base} "
            f"object={s['object'].upper()} light={s['light'].upper()}"
        )

    def write(self, data: bytes):
        cmd = data.decode(errors="ignore").strip().upper()
        if not cmd:
            return len(data)

        if cmd.startswith("UNLOCK"):
            ms = 2000
            bagian = cmd.split()
            if len(bagian) > 1 and bagian[1].isdigit():
                ms = max(500, min(3000, int(bagian[1])))
            self.sensor["solenoid"] = "on"
            self.sensor["lock"] = "released"
            self._antri(f"EVT SOLENOID ON {ms}")
            # Firmware hanya mengurus solenoid; lampu dikendalikan server.
            self._antri("EVT SOLENOID OFF", ms / 1000.0)
            self.sensor["solenoid"] = "off"
        elif cmd == "LOCK":
            self.sensor["solenoid"] = "off"
            self._antri("EVT SOLENOID OFF")
        elif cmd == "STATUS":
            self._status()
        elif cmd == "CAL":
            self.sensor["baseline"] = self.sensor["dist"]
            self.sensor["object"] = "empty"
            self._antri(f"OK CAL baseline={self.sensor['baseline']:.1f}")
        elif cmd == "LIGHT ON":
            self.sensor["light"] = "on"
            self._antri("EVT LIGHT ON")
        elif cmd == "LIGHT OFF":
            self.sensor["light"] = "off"
            self._antri("EVT LIGHT OFF")
        elif cmd == "PING":
            self._antri("PONG")
        else:
            self._antri(f"ERR perintah tidak dikenal: {cmd}")
        return len(data)

    def close(self):
        self.terbuka = False

    # ---------- skenario ----------
    def hint(self):
        return self._hint

    def skenario(self, nama: str):
        """Putar urutan event seperti loker sungguhan."""
        nama = (nama or "").lower()
        s = self.sensor

        if nama in ("paket_valid", "tanpa_resi", "tempelan_palsu", "penerima_tak_dikenal"):
            kelas = {
                "paket_valid": "ada_resi",
                "tanpa_resi": "tidak_ada_resi",
                "tempelan_palsu": "bukanresi_m",
                "penerima_tak_dikenal": "ada_resi",
            }[nama]
            self._hint = {"kelas": kelas,
                          "resi": "SPXID06889509307A" if nama == "paket_valid" else None,
                          "nama": None}
            s["door"] = "open"
            self._antri("EVT OBJECT PRESENT", 0.2)
            s["object"] = "present"
            self._antri("EVT DOOR CLOSED", 1.0)
            s["door"] = "closed"
            self._antri("EVT LOCK LOCKED", 1.3)
            s["lock"] = "locked"
            return True

        if nama == "buka_paksa":
            self._hint = None
            s["door"] = "open"
            s["object"] = "empty"
            self._antri("EVT DOOR OPEN", 0.2)
            self._antri("EVT ALERT DOOR_FORCED", 0.4)
            return True

        if nama == "pengambilan":
            self._hint = None
            s["solenoid"] = "on"
            self._antri("EVT SOLENOID ON 2000", 0.1)
            self._antri("EVT DOOR OPEN", 0.5)
            s["door"] = "open"
            self._antri("EVT OBJECT EMPTY", 1.2)
            s["object"] = "empty"
            self._antri("EVT SOLENOID OFF", 2.1)
            s["solenoid"] = "off"
            return True

        if nama == "reset":
            self.sensor = dict(_SENSOR_AWAL)
            self.sensor["dist"] = 30.0
            self.sensor["baseline"] = 30.0
            self._keluar.clear()
            self._hint = None
            return True
        return False


class SerialService:
    def __init__(self, cfg, on_event):
        self.cfg = cfg
        self.on_event = on_event
        self.mock = cfg.SERIAL_MODE == "mock"
        self._link = None
        self._thread = None
        self._jalan = False
        self._kunci = threading.Lock()
        self._tulis = queue.Queue(maxsize=64)
        self._snapshot = dict(_SENSOR_AWAL)
        self._jawab = queue.Queue()
        self.terakhir_error = None
        self.terhubung = False

    # ---------- siklus hidup ----------
    def mulai(self):
        if self._thread and self._thread.is_alive():
            return
        self._jalan = True
        self._thread = threading.Thread(target=self._loop, name="serial", daemon=True)
        self._thread.start()

    def berhenti(self):
        self._jalan = False
        if self._thread:
            self._thread.join(timeout=2)
        try:
            if self._link:
                self._link.close()
        except Exception:
            pass
        self._link = None

    def _buka(self):
        if self.mock:
            self._link = _MockLink(self.cfg.SERIAL_BAUD)
            self.terhubung = True
            log.info("Serial MOCK aktif (simulator firmware)")
            return
        import serial
        from serial.tools import list_ports

        port = self.cfg.SERIAL_PORT
        kandidat = [p for p in list_ports.comports()]
        if not any(p.device == port for p in kandidat):
            # Port tidak ada: coba temukan CH340 (VID 1A86) kalau diminta.
            vid = (self.cfg.SERIAL_AUTODETECT_VID or "").upper()
            cocok = [p.device for p in kandidat
                     if vid and vid in (p.hwid or "").upper()]
            if cocok:
                port = cocok[0]
                log.warning("Port %s tidak ada, memakai %s (VID %s)",
                            self.cfg.SERIAL_PORT, port, vid)
            else:
                raise RuntimeError(
                    f"Port {port} tidak ditemukan. Tersedia: "
                    f"{[p.device for p in kandidat] or 'tidak ada'}"
                )
        self._link = serial.Serial(port, self.cfg.SERIAL_BAUD,
                                   timeout=self.cfg.SERIAL_TIMEOUT)
        time.sleep(2.0)          # ESP32 reset saat port dibuka
        self._link.reset_input_buffer()
        self.terhubung = True
        self.terakhir_error = None
        log.info("Serial %s terbuka (%s baud)", port, self.cfg.SERIAL_BAUD)

    # ---------- thread ----------
    def _loop(self):
        while self._jalan:
            if self._link is None:
                try:
                    self._buka()
                    self._tulis.put(("PING", None))
                except Exception as exc:
                    self.terhubung = False
                    self.terakhir_error = str(exc)
                    log.warning("Gagal membuka serial: %s", exc)
                    time.sleep(3)
                    continue

            # Kirim perintah yang mengantre
            try:
                cmd, _ = self._tulis.get_nowait()
                self._link.write((cmd + "\n").encode())
                log.debug(">> %s", cmd)
            except queue.Empty:
                pass
            except Exception as exc:
                self.terakhir_error = str(exc)
                log.warning("Gagal mengirim: %s", exc)
                self._link = None
                continue

            # Baca satu baris (timeout pendek supaya loop tetap responsif)
            try:
                mentah = self._link.readline()
            except Exception as exc:
                self.terakhir_error = str(exc)
                log.warning("Serial terputus: %s", exc)
                try:
                    self._link.close()
                except Exception:
                    pass
                self._link = None
                self.terhubung = False
                continue

            if not mentah:
                time.sleep(0.02)
                continue

            baris = mentah.decode(errors="ignore").strip()
            if not baris:
                continue
            jenis, payload = urai_baris(baris)
            if jenis is None:
                log.debug("Baris diabaikan: %r", baris)
                continue

            if jenis == "STATUS":
                self._terapkan_status(payload)
            elif jenis in ("PONG", "OK", "ERR"):
                if jenis == "OK":
                    self._terapkan_ok(payload)
                self._jawab.put((jenis, payload))
            elif jenis == "INFO":
                log.info("ESP32: %s", payload.get("teks"))
            elif jenis == "EVT_TAK_DIKENAL":
                log.debug("Event tak dikenal: %s", payload)
            else:
                self._perbarui_snapshot(jenis, payload)
                try:
                    self.on_event(jenis, payload)
                except Exception:
                    log.exception("Handler event gagal untuk %s", jenis)

    def _terapkan_status(self, p):
        self._snapshot.update({
            "door": (p.get("door") or "OPEN").lower(),
            "lock": (p.get("lock") or "RELEASED").lower(),
            "solenoid": (p.get("solenoid") or "OFF").lower(),
            "object": (p.get("object") or "EMPTY").lower(),
            "light": (p.get("light") or "OFF").lower(),
        })
        for kunci in ("dist", "baseline"):
            nilai = p.get(kunci)
            try:
                self._snapshot[kunci] = float(nilai)
            except (TypeError, ValueError):
                self._snapshot[kunci] = None

    def _terapkan_ok(self, p):
        """'OK CAL baseline=25.0' membawa baseline; sinkronkan ke snapshot.

        Tanpa ini baseline hanya terisi kalau ada pesan STATUS, padahal
        aplikasi tidak meminta STATUS secara berkala.
        """
        m = re.search(r"baseline=([\d.]+)", p.get("teks", ""))
        if m:
            self._snapshot["baseline"] = float(m.group(1))

    def _perbarui_snapshot(self, jenis, payload):
        if jenis == "DOOR":
            self._snapshot["door"] = payload["state"]
        elif jenis == "LOCK":
            self._snapshot["lock"] = payload["state"]
        elif jenis == "OBJECT":
            self._snapshot["object"] = payload["state"]
        elif jenis == "LIGHT":
            self._snapshot["light"] = payload["state"]
        elif jenis == "SOLENOID":
            self._snapshot["solenoid"] = payload["state"]
        elif jenis == "DIST":
            self._snapshot["dist"] = payload.get("cm")

    # ---------- API ----------
    def send(self, cmd: str) -> bool:
        try:
            self._tulis.put_nowait((cmd.strip().upper(), None))
            return True
        except queue.Full:
            log.warning("Antrean perintah penuh, '%s' dibuang", cmd)
            return False

    def request(self, cmd: str, harap=("OK", "PONG"), timeout=1.5):
        self.send(cmd)
        batas = time.monotonic() + timeout
        while time.monotonic() < batas:
            try:
                jenis, payload = self._jawab.get(timeout=0.1)
            except queue.Empty:
                continue
            if jenis in harap:
                return payload
        return None

    def snapshot(self) -> dict:
        return dict(self._snapshot)

    def hint(self):
        return self._link.hint() if isinstance(self._link, _MockLink) else None

    def skenario(self, nama: str) -> bool:
        if isinstance(self._link, _MockLink):
            return self._link.skenario(nama)
        return False

    def info(self) -> dict:
        return {
            "mode": "mock" if self.mock else "real",
            "port": None if self.mock else self.cfg.SERIAL_PORT,
            "terhubung": self.terhubung,
            "error": self.terakhir_error,
        }
