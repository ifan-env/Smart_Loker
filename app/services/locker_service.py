"""State machine loker: inti sistem.

Satu-satunya penulis `locker_state` dan `events`. Semua event masuk lewat
antrean (:meth:`kirim_event`), jadi tidak ada dua thread yang menulis DB
bersamaan. Analisis gambar dikerjakan thread terpisah supaya event sensor
(DOOR/LOCK/RFID) tidak tertahan 1-3 detik saat SVM+OCR berjalan.

Alur lengkap ada di AGENTS.md bagian 3, plus tiga perbaikan:
  1. auto-unlock dibatasi (tidak berputar terus saat loker kosong terkunci),
  2. baseline disimpan di DB dan disinkronkan setelah ESP32 reset,
  3. DOOR_FORCED saat pengambilan tidak dianggap peringatan.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import (Deposit, Event, LockerState, Parcel, PickupCode,
                      Resident, db, sekarang)
from . import matcher, otp, resi_service, svm_service

log = logging.getLogger(__name__)

# ---------- nama state ----------
IDLE_OPEN = "IDLE_OPEN"
PAKET_MASUK = "PAKET_MASUK"
MENUNGGU_TENANG = "MENUNGGU_TENANG"
ANALISIS = "ANALISIS"
TERSIMPAN = "TERSIMPAN"
PERLU_MANUAL = "TERSIMPAN_PERLU_MANUAL"
PENGAMBILAN = "PENGAMBILAN"
DIAMBIL = "DIAMBIL"
TUNGGU_ADMIN = "TUNGGU_ADMIN"


def _kini_monoton():
    return time.monotonic()


class LockerService:
    def __init__(self, app, cfg, serial, camera, notifier, bus):
        self.app = app
        self.cfg = cfg
        self.serial = serial
        self.camera = camera
        self.notifier = notifier
        self.bus = bus

        self._q: queue.Queue = queue.Queue()
        self._analisis_q: queue.Queue = queue.Queue(maxsize=2)
        self._kunci = threading.RLock()
        self._jalan = False
        self._thread = None
        self._worker = None

        # cermin state di memori (sumber kebenaran saat runtime, disimpan ke DB)
        self.state = IDLE_OPEN
        self.sejak = sekarang()
        self.reject_count = 0
        self.auto_unlock_count = 0
        self.auto_unlock_terakhir = 0.0
        self.analysis_token = None
        self.deposit_id = None
        self.baseline_cm = None

        # timer internal (monotonic, tidak ikut tersimpan)
        self._tenang_sampai = 0.0
        self._analisis_sampai = 0.0
        self._kedip = {"sisa": 0, "berikut": 0.0}
        self._lampu_sampai = 0.0
        self._kosong_sejak = 0.0
        self._pintu_terbuka_sejak = 0.0
        self._pengambilan_sampai = 0.0
        self._tombol_terakhir = 0.0
        self._sinkron_baseline = False
        self._cache_ringkasan = None
        self.catatan = None

        # Mode daftar kartu: tap RFID berikutnya ditahan di sini untuk admin.
        self._daftar_kartu: dict | None = None

    # =================================================================
    # siklus hidup
    # =================================================================
    def mulai(self):
        if self._thread and self._thread.is_alive():
            return
        self._jalan = True
        with self.app.app_context():
            self._pulihkan()
        self._thread = threading.Thread(target=self._loop, name="locker", daemon=True)
        self._worker = threading.Thread(target=self._loop_analisis, name="analisis", daemon=True)
        self._thread.start()
        self._worker.start()
        log.info("LockerService jalan (state=%s)", self.state)

    def berhenti(self):
        self._jalan = False
        for t in (self._thread, self._worker):
            if t:
                t.join(timeout=3)

    def _pulihkan(self):
        baris = db.session.get(LockerState, 1)
        if baris is None:
            baris = LockerState(id=1, state=IDLE_OPEN)
            db.session.add(baris)
            db.session.commit()
        self.state = baris.state or IDLE_OPEN
        self.sejak = baris.since or sekarang()
        self.reject_count = baris.reject_count or 0
        self.auto_unlock_count = baris.auto_unlock_count or 0
        self.baseline_cm = baris.baseline_cm
        self.catatan = baris.catatan
        self.deposit_id = baris.active_deposit_id
        self.analysis_token = baris.analysis_token
        # Sesi yang terputus saat aplikasi mati tidak boleh dianggap berjalan.
        if self.state in (MENUNGGU_TENANG, ANALISIS):
            log.warning("State %s terputus saat restart, kembali ke PAKET_MASUK", self.state)
            self.state = PAKET_MASUK
        self._simpan()
        self._catat("SISTEM", "info", {"pesan": "LockerService start",
                                       "state": self.state})

    # =================================================================
    # antrean event & loop
    # =================================================================
    def kirim_event(self, jenis: str, payload: dict):
        self._q.put((jenis, payload))

    def _loop(self):
        while self._jalan:
            try:
                item = self._q.get(timeout=0.1)
            except queue.Empty:
                item = None
            with self._kunci:
                with self.app.app_context():
                    try:
                        if item:
                            self._tangani(item[0], item[1])
                        self._tick()
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        log.exception("Gagal memproses event %s", item)
            if self.bus.jumlah_klien:
                with self.app.app_context():
                    self.siar_status()

    def _loop_analisis(self):
        while self._jalan:
            try:
                token, deposit_id = self._analisis_q.get(timeout=0.2)
            except queue.Empty:
                continue
            with self.app.app_context():
                hasil = self._analisis(token, deposit_id)
            self._q.put(("HASIL_ANALISIS", hasil))

    # =================================================================
    # penanganan event
    # =================================================================
    def _tangani(self, jenis, p):
        if jenis == "HASIL_ANALISIS":
            self._tangani_hasil(p)
        elif jenis == "DOOR":
            self._catat("DOOR", "info", p)
            if p["state"] == "closed":
                self._pintu_terbuka_sejak = 0.0
                self._mungkin_mulai_tenang()
                if (self.state in (IDLE_OPEN, DIAMBIL)
                        and self.serial.snapshot().get("object") == "empty"):
                    self._lampu(False)      # pintu ditutup tanpa paket
            else:
                self._pintu_terbuka_sejak = _kini_monoton()
                self._pengambilan_sampai = 0.0
                if self.state == MENUNGGU_TENANG:
                    self.state = PAKET_MASUK
                    self._tenang_sampai = 0.0
        elif jenis == "LOCK":
            self._catat("LOCK", "info", p)
            if p["state"] == "locked":
                self._mungkin_mulai_tenang()
        elif jenis == "BUTTON":
            self._tangani_tombol(p)
        elif jenis == "OBJECT":
            self._tangani_object(p)
        elif jenis == "LIGHT":
            pass
        elif jenis == "SOLENOID":
            pass
        elif jenis == "DIST":
            pass
        elif jenis == "RFID":
            self._tangani_rfid(p)
        elif jenis == "ALERT":
            self._tangani_alert(p)
        elif jenis == "EVT_TAK_DIKENAL":
            self._catat("PROTOKOL", "info", p)

    def _mungkin_mulai_tenang(self):
        if self.state != PAKET_MASUK:
            return
        sensor = self.serial.snapshot()
        if sensor.get("door") != "closed" or sensor.get("lock") != "locked":
            return
        self._tenang_sampai = _kini_monoton() + self.cfg.TENANG_MS / 1000.0
        self._ubah_state(MENUNGGU_TENANG, catatan="paket diam, siap dianalisis")

    def _tangani_object(self, p):
        self._catat("OBJECT", "info", p)
        present = p["state"] == "present"

        if present:
            self._kosong_sejak = 0.0
            if self.state in (IDLE_OPEN, DIAMBIL):
                self.reject_count = 0
                self.auto_unlock_count = 0
                self._ubah_state(PAKET_MASUK, catatan="benda terdeteksi")
                self._lampu(True)
            return

        # kosong
        self._kosong_sejak = _kini_monoton()
        if self.state in (PAKET_MASUK, MENUNGGU_TENANG):
            self.reject_count = 0
            self._lampu(False)
            self._ubah_state(IDLE_OPEN, catatan="benda diambil kembali")
        elif self.state == PENGAMBILAN:
            self._selesaikan_pengambilan()

    def _tangani_rfid(self, p):
        uid = (p.get("uid") or "").upper()
        self._catat("RFID", "info", {"uid": uid})
        if not uid:
            return
        if self._daftar_kartu is not None:
            self._tampung_tap(uid)
            return
        warga = Resident.query.filter_by(rfid_uid=uid, aktif=True).first()
        if warga is None:
            self._catat("RFID_TAK_DIKENAL", "warning", {"uid": uid})
            return
        if self.state not in (TERSIMPAN, PENGAMBILAN):
            self._catat("RFID_DIABAIKAN", "info",
                        {"uid": uid, "state": self.state,
                         "alasan": "tidak ada paket menunggu diambil"})
            return
        if (self.state == PENGAMBILAN
                and self.serial.snapshot().get("door") != "closed"):
            self._catat("RFID_DIABAIKAN", "info",
                        {"uid": uid, "state": self.state,
                         "alasan": "pintu masih terbuka"})
            return
        parcel = (Parcel.query.filter_by(resident_id=warga.id, status="tersimpan")
                  .order_by(Parcel.stored_at.desc()).first())
        if parcel is None:
            self._catat("RFID_TANPA_PAKET", "info", {"uid": uid, "nama": warga.nama})
            return
        self._buka_untuk_pengambilan(warga, parcel, metode="rfid")

    def _tangani_tombol(self, p):
        """Tombol fisik 'Titip Paket' di luar loker.

        Firmware hanya melapor tekanan; keputusan buka ada di sini supaya
        tombol tidak bisa membuka loker yang sedang berisi paket.
        """
        if p.get("state") != "press":
            return
        kini = _kini_monoton()
        sensor = self.serial.snapshot()
        alasan = None
        if self.state not in (IDLE_OPEN, DIAMBIL):
            alasan = f"state {self.state}"
        elif sensor.get("object") != "empty":
            alasan = "ada benda di dalam"
        elif sensor.get("door") != "closed":
            alasan = "pintu sudah terbuka"
        elif kini - self._tombol_terakhir < self.cfg.TOMBOL_JEDA_S:
            alasan = "terlalu cepat"
        elif Parcel.query.filter_by(status="tersimpan").first() is not None:
            alasan = "masih ada paket tersimpan"
        if alasan:
            self._catat("TOMBOL_DITOLAK", "info", {"alasan": alasan})
            self._mulai_kedip(2)
            return
        self._tombol_terakhir = kini
        self._lampu(True)
        self.serial.send(f"UNLOCK {int(self.cfg.UNLOCK_MS)}")
        self._catat("TOMBOL_TITIP", "info", {})

    def _tangani_alert(self, p):
        kind = p.get("kind", "UNKNOWN")
        severity = "warning"
        if kind == "DOOR_FORCED" and self.state in (IDLE_OPEN, PENGAMBILAN):
            severity = "info"        # pintu memang boleh terbuka di kondisi ini
        self._catat(f"ALERT_{kind}", severity, p)
        if severity == "warning":
            self.notifier.anomali(
                f"{kind} terdeteksi",
                f"State loker: {self.state}",
            )

    # =================================================================
    # analisis gambar
    # =================================================================
    def _mulai_analisis(self):
        token = uuid.uuid4().hex
        dep = Deposit(captured_at=sekarang(), attempt_no=self.reject_count + 1)
        db.session.add(dep)
        db.session.flush()
        self.deposit_id = dep.id
        self.analysis_token = token
        self._analisis_sampai = _kini_monoton() + self.cfg.ANALYSIS_TIMEOUT_S
        self._ubah_state(ANALISIS, catatan=f"deposit #{dep.id}")
        try:
            self._analisis_q.put_nowait((token, dep.id))
        except queue.Full:
            log.warning("Worker analisis sibuk, siklus dibatalkan")
            self._ke_admin("Worker analisis sibuk", {"deposit_id": dep.id})

    def _analisis(self, token, deposit_id):
        """Berjalan di thread ANALISIS. Tidak menulis DB."""
        try:
            img = self.camera.ambil()
            foto = self._simpan_foto(img, deposit_id)
            svm = svm_service.klasifikasi(img)
            baca = resi_service.baca(img)

            hint = self.serial.hint()
            if hint:      # mode simulasi: kelas & resi ditentukan skenario
                if hint.get("kelas"):
                    svm = {"kelas": hint["kelas"], "margin": 99.0,
                           "skor": {hint["kelas"]: 99.0}, "ragu": False,
                           "simulasi": True}
                if hint.get("resi"):
                    baca = dict(baca, resi=hint["resi"], sumber="simulasi",
                                catatan="mode simulasi")
            return {"token": token, "deposit_id": deposit_id, "foto": foto,
                    "svm": svm, "resi": baca, "error": None}
        except Exception as exc:
            log.exception("Analisis gagal")
            return {"token": token, "deposit_id": deposit_id, "foto": None,
                    "svm": None, "resi": None, "error": str(exc)}

    def _simpan_foto(self, img, deposit_id):
        self.cfg.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        nama = f"deposit-{deposit_id}-{uuid.uuid4().hex[:8]}.jpg"
        jalur = Path(self.cfg.UPLOAD_DIR) / nama
        try:
            img.convert("RGB").save(jalur, quality=88)
            return nama
        except Exception:
            log.exception("Gagal menyimpan foto bukti")
            return None

    def _tangani_hasil(self, h):
        if h["token"] != self.analysis_token:
            log.info("Hasil analisis kedaluwarsa, dibuang")
            return
        self.analysis_token = None
        dep = db.session.get(Deposit, h["deposit_id"])
        if dep is None:
            self._ubah_state(IDLE_OPEN)
            return

        dep.photo_path = h.get("foto")
        if h["error"]:
            dep.result = "perlu_manual"
            db.session.commit()
            self._ke_admin("Analisis gambar gagal", {
                "deposit_id": dep.id, "error": h["error"]})
            return

        svm, resi = h["svm"], h["resi"]
        dep.svm_kelas = svm["kelas"]
        dep.svm_margin = svm["margin"]
        dep.svm_skor = json.dumps(svm.get("skor", {}))
        dep.ocr_resi = resi.get("resi")
        dep.ocr_nama = resi.get("nama")
        dep.raw_ocr = json.dumps({k: resi.get(k) for k in
                                  ("teks", "kandidat", "sumber", "catatan")},
                                 ensure_ascii=False)
        self._catat("ANALISIS", "info", {
            "deposit_id": dep.id, "svm": svm, "resi": resi.get("resi"),
            "nama": resi.get("nama"), "sumber": resi.get("sumber"),
        }, foto=dep.photo_path)

        if svm["kelas"] != "ada_resi":
            self._tolak(dep, f"SVM: {svm['kelas']}")
            return
        if svm.get("ragu"):
            self._tolak(dep, f"margin rendah ({svm['margin']})")
            return

        parcel, resident, metode, skor = self._cocokkan(resi, dep)
        if parcel is None and resident is None:
            dep.result = "perlu_manual"
            self._ubah_state(PERLU_MANUAL, catatan=f"deposit #{dep.id} tak dikenali")
            self._lampu(False)
            self.notifier.perlu_manual(
                dep.id, svm["kelas"],
                f"resi={resi.get('resi')} nama={resi.get('nama')} skor={skor}",
            )
            self._catat("PERLU_MANUAL", "warning",
                        {"deposit_id": dep.id, "ocr_resi": resi.get("resi"),
                         "ocr_nama": resi.get("nama")}, foto=dep.photo_path)
            return

        if parcel is None:                       # dikenali dari nama
            parcel = Parcel(resident_id=resident.id,
                            tracking_number=resi.get("resi"),
                            tracking_norm=matcher.normalisasi(resi.get("resi") or ""),
                            status="tersimpan", stored_at=sekarang())
            db.session.add(parcel)
        else:
            parcel.status = "tersimpan"
            parcel.stored_at = sekarang()
            resident = parcel.resident

        db.session.flush()
        dep.parcel_id = parcel.id
        dep.match_method = metode
        dep.result = "diterima"
        self._ubah_state(TERSIMPAN, catatan=f"paket #{parcel.id}")
        self._lampu(False)

        kode = self._buat_otp(parcel)
        self._catat("PAKET_TERSIMPAN", "info",
                    {"parcel_id": parcel.id, "resident_id": resident.id,
                     "metode": metode, "skor": skor},
                    foto=dep.photo_path)
        self.notifier.paket_masuk(resident, parcel, kode)

    def _cocokkan(self, resi, dep):
        """Cari parcel/penghuni yang cocok. -> (parcel, resident, metode, skor)"""
        nomor = resi.get("resi")
        if nomor:
            kandidat = Parcel.query.filter(Parcel.status.in_(("menunggu",))).all()
            parcel, skor, metode = matcher.cocok_resi(
                nomor, kandidat, jarak_maks=self.cfg.RESI_JARAK_MAKS)
            if parcel is not None:
                return parcel, parcel.resident, metode, skor

        nama = resi.get("nama")
        if nama:
            warga = Resident.query.filter_by(aktif=True, role="penghuni").all()
            resident, skor, ambigu = matcher.cocok_nama(
                nama, warga, self.cfg.NAMA_MIN_RASIO, self.cfg.NAMA_AMBIGU_GAP)
            if resident is not None and not ambigu:
                return None, resident, "nama", skor
            if ambigu:
                self._catat("NAMA_AMBIGU", "warning", {"nama": nama, "skor": skor})
        return None, None, None, 0.0

    def _tolak(self, dep, alasan):
        dep.result = "ditolak"
        self.reject_count += 1
        self._catat("DITOLAK", "info",
                    {"deposit_id": dep.id, "alasan": alasan,
                     "percobaan": self.reject_count}, foto=dep.photo_path)

        if self.reject_count >= self.cfg.BATAS_TOLAK:
            self._ke_admin("Batas penolakan tercapai", {
                "deposit_id": dep.id, "alasan": alasan})
            self._ubah_state(TUNGGU_ADMIN, catatan=f"tolak {self.reject_count}x")
            self.serial.send(f"UNLOCK {int(self.cfg.UNLOCK_MS)}")
            return

        self.serial.send(f"UNLOCK {int(self.cfg.UNLOCK_MS)}")
        self._mulai_kedip(4)
        # Paket masih di dalam: tunggu pintu ditutup lagi untuk siklus baru.
        self._ubah_state(PAKET_MASUK, catatan=f"ditolak: {alasan}")

    def _mulai_kedip(self, jumlah: int):
        """Umpan balik ke kurir: lampu berkedip (tidak ada buzzer/layar)."""
        self._kedip = {"sisa": jumlah * 2, "berikut": 0.0}

    def _lampu(self, nyala: bool):
        """Nyalakan/matikan lampu sambil mencatat sisa waktunya.

        Firmware mematikan lampu setelah 2 menit; server menghitung sisa yang
        sama supaya dashboard bisa menampilkan hitung mundur yang jujur.
        """
        self.serial.send("LIGHT ON" if nyala else "LIGHT OFF")
        if nyala:
            self._lampu_sampai = time.monotonic() + self.cfg.LAMPU_MAKS_DETIK
        else:
            self._lampu_sampai = 0.0

    def sisa_lampu(self) -> int:
        if not self._lampu_sampai:
            return 0
        return max(0, int(self._lampu_sampai - time.monotonic()))

    def _buat_otp(self, parcel):
        kode = otp.baru(self.cfg.OTP_PANJANG)
        PickupCode.query.filter_by(parcel_id=parcel.id, used_at=None).update(
            {"used_at": sekarang()})
        db.session.add(PickupCode(
            parcel_id=parcel.id,
            code_hash=otp.buat(kode),
            expires_at=otp.kedaluwarsa(self.cfg.OTP_MENIT),
        ))
        log.info("OTP paket #%s: %s", parcel.id, kode)
        return kode

    # =================================================================
    # pengambilan
    # =================================================================
    def _buka_untuk_pengambilan(self, resident, parcel, metode="rfid"):
        self._lampu(True)
        ms = int(self.cfg.UNLOCK_MS)
        self.serial.send(f"UNLOCK {ms}")
        # Pintu terbuka sendiri (kait mendorong). Beri tenggang 10 dtk kalau
        # pintu tertahan; setelah itu pengambilan dibatalkan agar tidak macet.
        self._pengambilan_sampai = _kini_monoton() + ms / 1000.0 + 10.0
        self._ubah_state(PENGAMBILAN,
                         catatan=f"parcel #{parcel.id} oleh {resident.nama} ({metode})")
        self._catat("PENGAMBILAN_DIMULAI", "info",
                    {"parcel_id": parcel.id, "metode": metode})

    def _selesaikan_pengambilan(self):
        parcel = (Parcel.query.filter_by(status="tersimpan")
                  .order_by(Parcel.stored_at.desc()).first())
        if parcel:
            parcel.status = "diambil"
            parcel.picked_at = sekarang()
            self._catat("DIAMBIL", "info", {"parcel_id": parcel.id})
        self._pengambilan_sampai = 0.0
        self._lampu(False)
        self._ubah_state(DIAMBIL, catatan="paket diambil")

    def verifikasi_otp(self, kode: str):
        """Dipanggil route /ambil. -> (ok, pesan)"""
        with self._kunci:
            with self.app.app_context():
                kode = (kode or "").strip()
                if len(kode) != self.cfg.OTP_PANJANG or not kode.isdigit():
                    return False, "Kode harus angka."
                calon = (PickupCode.query.filter(PickupCode.used_at.is_(None),
                                                 PickupCode.expires_at > sekarang())
                         .order_by(PickupCode.id.desc()).all())
                cocok = None
                for pc in calon:
                    if pc.failed_attempts >= self.cfg.OTP_MAKS_PERCOBAAN:
                        continue
                    if otp.periksa(pc.code_hash, kode):
                        cocok = pc
                        break
                if cocok is None:
                    for pc in calon:
                        pc.failed_attempts += 1
                    db.session.commit()
                    self._catat("OTP_GAGAL", "warning", {"percobaan": len(calon)})
                    return False, "Kode salah atau kedaluwarsa."

                parcel = db.session.get(Parcel, cocok.parcel_id)
                if parcel is None or parcel.status != "tersimpan":
                    return False, "Paket sudah diambil."
                cocok.used_at = sekarang()
                db.session.commit()
                self._buka_untuk_pengambilan(parcel.resident, parcel, metode="otp")
                return True, "Kode benar. Pintu terbuka, silakan ambil paket."

    # =================================================================
    # pendaftaran kartu RFID (mode tap-to-register, dari halaman admin)
    # =================================================================
    def mulai_daftar_kartu(self, resident_id: int | None = None,
                           detik: int = 120) -> dict:
        """Buka jendela tunggu tap kartu untuk form tambah/edit penghuni.

        Selama mode ini aktif, tap RFID dari kartu mana pun ditahan di
        :attr:`_daftar_kartu` dan TIDAK diproses sebagai pengambilan paket.
        `resident_id=None` dipakai saat menambah penghuni baru, karena
        penghuninya belum ada ketika kartu ditempel.
        """
        with self._kunci:
            with self.app.app_context():
                warga = None
                if resident_id is not None:
                    warga = db.session.get(Resident, resident_id)
                    if warga is None:
                        return {"ok": False, "error": "Penghuni tidak ditemukan."}
                detik = max(20, min(int(detik), 300))
                token = uuid.uuid4().hex[:12]
                self._daftar_kartu = {
                    "token": token,
                    "resident_id": warga.id if warga else None,
                    "nama": warga.nama if warga else None,
                    "batas": time.monotonic() + detik,
                    "detik": detik,
                    "uid": None,
                    "pemilik": None,
                    "waktu": None,
                }
                self._catat("PANTAU_KARTU", "info",
                            {"resident_id": warga.id if warga else None,
                             "nama": warga.nama if warga else None,
                             "detik": detik})
                self.siar_status()
                return {"ok": True, "data": {"token": token, "detik": detik}}

    def batal_daftar_kartu(self) -> dict:
        with self._kunci:
            with self.app.app_context():
                if self._daftar_kartu is None:
                    return {"ok": True}
                self._catat("PANTAU_KARTU_BATAL", "info",
                            {"resident_id": self._daftar_kartu["resident_id"]})
                self._daftar_kartu = None
                self.siar_status()
                return {"ok": True}

    def _tampung_tap(self, uid: str):
        """Simpan kartu yang ditempel selama mode daftar aktif.

        Pemilik kartu ikut diperiksa di sini supaya form bisa langsung
        memberi tahu "kartu ini milik siapa" sebelum disimpan.
        """
        mode = self._daftar_kartu
        if mode["uid"] == uid:
            return
        pemilik = Resident.query.filter_by(rfid_uid=uid).first()
        mode.update({
            "uid": uid,
            "waktu": sekarang(),
            "pemilik": None if pemilik is None else {
                "id": pemilik.id, "nama": pemilik.nama,
                "kamar": pemilik.nomor_kamar,
            },
        })
        self._catat("KARTU_TERBACA", "info",
                    {"uid": uid, "resident_id": mode["resident_id"]})
        self.siar_status()

    def _tutup_mode_daftar_kartu(self, alasan: str):
        mode = self._daftar_kartu
        self._daftar_kartu = None
        self._catat("PANTAU_KARTU_SELESAI", "info",
                    {"resident_id": mode["resident_id"], "alasan": alasan})
        self.siar_status()

    def info_daftar_kartu(self) -> dict | None:
        """Ringkasan mode daftar untuk API. Tidak mengubah apa pun."""
        mode = self._daftar_kartu
        if mode is None:
            return None
        sisa = max(0, int(mode["batas"] - time.monotonic()))
        hasil = {
            "token": mode["token"],
            "resident_id": mode["resident_id"],
            "nama": mode["nama"],
            "uid": mode["uid"],
            "pemilik": mode["pemilik"],
            "sisa": sisa,
        }
        if mode["uid"] and mode["waktu"]:
            hasil["waktu"] = mode["waktu"].isoformat()
        return hasil

    def info_daftar_kartu_hidup(self) -> dict | None:
        """Sama seperti :meth:`info_daftar_kartu`, tapi menutup mode yang habis."""
        with self._kunci:
            if self._daftar_kartu and time.monotonic() > self._daftar_kartu["batas"]:
                self._tutup_mode_daftar_kartu("waktu habis")
            return self.info_daftar_kartu()

    # =================================================================
    # timer
    # =================================================================
    def _tick(self):
        kini = _kini_monoton()

        if self._kedip["sisa"] > 0 and kini >= self._kedip["berikut"]:
            # Mulai dari ON supaya jumlah kedipan genap berakhir OFF.
            self.serial.send("LIGHT ON" if self._kedip["sisa"] % 2 == 0 else "LIGHT OFF")
            self._kedip["sisa"] -= 1
            self._kedip["berikut"] = kini + 0.25

        if self.state == MENUNGGU_TENANG and self._tenang_sampai and kini >= self._tenang_sampai:
            self._tenang_sampai = 0.0
            self._mulai_analisis()
            return

        if self.state == ANALISIS and kini > self._analisis_sampai:
            self.analysis_token = None
            self._ke_admin("Analisis melewati batas waktu", {"state": self.state})
            return

        # Pintu dibiarkan terbuka kelamaan saat sudah ada paket
        if self._pintu_terbuka_sejak and self.state in (TERSIMPAN, PERLU_MANUAL):
            if kini - self._pintu_terbuka_sejak > self.cfg.PINTU_TERBUKA_MAKS_S:
                self._pintu_terbuka_sejak = kini
                self._catat("PINTU_TERBUKA_LAMA", "warning", {"state": self.state})

        # Paket hilang dari loker yang seharusnya terkunci
        if self.state == TERSIMPAN and self.serial.snapshot().get("object") == "empty":
            if not self._kosong_sejak:
                self._kosong_sejak = kini
            elif kini - self._kosong_sejak > 3:
                self._kosong_sejak = kini
                self._catat("PAKET_HILANG", "warning", {"state": self.state})
                self.notifier.anomali("Paket hilang dari loker",
                                      "Ada paket tersimpan tapi sensor mendeteksi kosong.")

        # Pengambilan tidak jadi (pintu tertahan / tidak dibuka)
        sensor = self.serial.snapshot()
        if (self.state == PENGAMBILAN and self._pengambilan_sampai
                and kini > self._pengambilan_sampai
                and sensor.get("door") == "closed"):
            self._pengambilan_sampai = 0.0
            self._lampu(False)
            self._catat("PENGAMBILAN_BATAL", "info",
                        {"alasan": "pintu tidak terbuka"})
            self._ubah_state(TERSIMPAN, catatan="pengambilan batal, pintu tertutup")

        # Loker kosong tapi terkunci tanpa sesi aktif -> buka sekali.
        # Dilewati untuk kunci pegas: "kosong + terkunci" adalah kondisi normal.
        if (not self.cfg.KUNCI_FAIL_SECURE
                and self.state == IDLE_OPEN and sensor.get("lock") == "locked"
                and sensor.get("object") == "empty"):
            if kini - self.auto_unlock_terakhir > self.cfg.AUTO_UNLOCK_JEDA_S:
                if self.auto_unlock_count >= self.cfg.AUTO_UNLOCK_MAKS_PER_JAM:
                    self._ubah_state(TUNGGU_ADMIN, catatan="auto-unlock melebihi batas")
                    self._ke_admin("Loker terkunci dalam keadaan kosong berulang",
                                   {"count": self.auto_unlock_count})
                else:
                    self.auto_unlock_terakhir = kini
                    self.auto_unlock_count += 1
                    self.serial.send("UNLOCK 2000")
                    self._catat("AUTO_UNLOCK", "warning",
                                {"count": self.auto_unlock_count})

        # Simpan baseline yang dilaporkan firmware agar tidak hilang di DB/UI.
        baseline = sensor.get("baseline")
        if baseline is not None and baseline != self.baseline_cm:
            self.baseline_cm = baseline
            self._simpan()

        # Sinkronkan baseline setelah ESP32 reset. Ukur saat pintu TERTUTUP:
        # dengan pintu terbuka, gelombang ultrasonik keluar loker dan baseline
        # jadi tidak masuk akal (pernah terbaca 2.6 cm).
        if not self._sinkron_baseline and sensor.get("baseline") is None:
            if sensor.get("door") == "closed" and sensor.get("object") == "empty":
                self.serial.send("CAL")
                self.serial.send("STATUS")
                self._sinkron_baseline = True
                self._catat("KALIBRASI_OTOMATIS", "info",
                            {"alasan": "baseline hilang setelah reset"})

    # =================================================================
    # aksi admin & status
    # =================================================================
    def _ada_paket_di_dalam(self) -> bool:
        """True kalau loker sedang berisi paket (fisik atau tercatat).

        Dipakai untuk menahan tombol "Buka kunci" admin: loker berisi paket
        hanya boleh dibuka lewat RFID/OTP, supaya paket tidak bisa diambil
        orang yang tidak berhak.
        """
        if self.serial.snapshot().get("object") == "present":
            return True
        if self.state in (PAKET_MASUK, MENUNGGU_TENANG, ANALISIS,
                          TERSIMPAN, PERLU_MANUAL, PENGAMBILAN):
            return True
        return Parcel.query.filter_by(status="tersimpan").first() is not None

    def aksi_admin(self, aksi: str, **kw) -> dict:
        with self._kunci:
            with self.app.app_context():
                aksi = (aksi or "").lower()
                if aksi == "unlock":
                    if self._ada_paket_di_dalam():
                        self._catat("BUKA_DITOLAK", "warning",
                                    {"alasan": "ada paket di dalam",
                                     "state": self.state})
                        return {"ok": False,
                                "error": "Loker berisi paket — buka lewat RFID/OTP."}
                    ms = int(kw.get("ms") or self.cfg.UNLOCK_MS)
                    self.serial.send(f"UNLOCK {ms}")
                elif aksi == "unlock_paksa":
                    if not self.serial.terhubung:
                        return {"ok": False,
                                "error": "Serial ESP32 belum terhubung."}
                    sensor = self.serial.snapshot()
                    self._lampu(True)
                    self.serial.send(f"UNLOCK {int(self.cfg.UNLOCK_MS)}")
                    self._catat("BUKA_PAKSA", "warning", {
                        "state": self.state,
                        "object": sensor.get("object"),
                        "door": sensor.get("door"),
                        "dist": sensor.get("dist"),
                    })
                    self.notifier.anomali(
                        "Buka paksa oleh admin",
                        f"state={self.state} object={sensor.get('object')} "
                        f"door={sensor.get('door')}")
                    return {"ok": True, "darurat": True}
                elif aksi == "lock":
                    self.serial.send("LOCK")
                elif aksi == "cal":
                    self.serial.send("CAL")
                    self.serial.send("STATUS")
                    self._sinkron_baseline = True
                elif aksi == "kamera":
                    return self.camera.cek()
                elif aksi == "light":
                    self._lampu(bool(kw.get("on", True)))
                elif aksi == "reset":
                    self.reject_count = 0
                    self.auto_unlock_count = 0
                    self.analysis_token = None
                    self._pengambilan_sampai = 0.0
                    self._lampu(False)
                    self._ubah_state(IDLE_OPEN, catatan="reset manual admin")
                elif aksi == "assign":
                    return self._assign(int(kw["deposit_id"]), int(kw["resident_id"]))
                elif aksi == "simulasi":
                    if not self.serial.skenario(kw.get("nama", "")):
                        return {"ok": False, "error": "Simulasi hanya di mode mock"}
                else:
                    return {"ok": False, "error": f"aksi tidak dikenal: {aksi}"}
                self._catat(f"ADMIN_{aksi.upper()}", "info", {k: str(v) for k, v in kw.items()})
                db.session.commit()
                return {"ok": True}

    def _assign(self, deposit_id, resident_id):
        dep = db.session.get(Deposit, deposit_id)
        warga = db.session.get(Resident, resident_id)
        if dep is None or warga is None:
            return {"ok": False, "error": "Deposit atau penghuni tidak ditemukan"}

        parcel = db.session.get(Parcel, dep.parcel_id) if dep.parcel_id else None
        if parcel is None:
            parcel = Parcel(resident_id=warga.id,
                            tracking_number=dep.ocr_resi,
                            tracking_norm=matcher.normalisasi(dep.ocr_resi or ""),
                            status="tersimpan", stored_at=sekarang())
            db.session.add(parcel)
            db.session.flush()
        else:
            parcel.resident_id = warga.id
            parcel.status = "tersimpan"
            parcel.stored_at = sekarang()

        dep.parcel_id = parcel.id
        dep.match_method = "manual"
        dep.result = "diterima"
        self._ubah_state(TERSIMPAN, catatan=f"assign manual parcel #{parcel.id}")
        kode = self._buat_otp(parcel)
        self.notifier.paket_masuk(warga, parcel, kode)
        self._catat("ASSIGN_MANUAL", "info",
                    {"deposit_id": dep.id, "parcel_id": parcel.id,
                     "resident_id": warga.id})
        db.session.commit()
        return {"ok": True, "parcel_id": parcel.id}

    def _ringkasan_terkini(self) -> dict:
        """Hasil analisis, foto, dan event terakhir. Di-cache sampai ada catatan baru."""
        if self._cache_ringkasan is not None:
            return self._cache_ringkasan
        dep = Deposit.query.order_by(Deposit.id.desc()).first()
        berfoto = (Deposit.query.filter(Deposit.photo_path.isnot(None))
                   .order_by(Deposit.id.desc()).first())
        events = Event.query.order_by(Event.id.desc()).limit(6).all()
        analisis = None
        if dep is not None:
            analisis = {
                "id": dep.id,
                "waktu": dep.captured_at.isoformat() if dep.captured_at else None,
                "kelas": dep.svm_kelas,
                "margin": dep.svm_margin,
                "ambang": self.cfg.SVM_MIN_MARGIN,
                "resi": dep.ocr_resi,
                "nama": dep.ocr_nama,
                "metode": dep.match_method,
                "hasil": dep.result,
                "percobaan": dep.attempt_no,
            }
        self._cache_ringkasan = {
            "analisis": analisis,
            "foto_terakhir": berfoto.photo_path if berfoto else None,
            "event_terakhir": [{
                "type": e.type,
                "severity": e.severity,
                "waktu": e.created_at.isoformat() if e.created_at else None,
            } for e in events],
        }
        return self._cache_ringkasan

    def status(self) -> dict:
        ringkas = self._ringkasan_terkini()
        return {
            "state": self.state,
            "sejak": self.sejak.isoformat() if self.sejak else None,
            "catatan": self.catatan,
            "sensor": self.serial.snapshot(),
            "serial": self.serial.info(),
            "lampu_sisa": self.sisa_lampu(),
            "reject_count": self.reject_count,
            "auto_unlock_count": self.auto_unlock_count,
            "deposit_id": self.deposit_id,
            "kamera": self.cfg.CAMERA_MODE,
            "svm": svm_service.meta(),
            "daftar_kartu": self.info_daftar_kartu(),
            **ringkas,
        }

    def siar_status(self):
        self.bus.siar({"tipe": "status", "data": self.status()})

    # =================================================================
    # util
    # =================================================================
    def _ubah_state(self, baru, catatan=""):
        lama = self.state
        if baru == lama and not catatan:
            return
        self.state = baru
        self.sejak = sekarang()
        self.catatan = catatan
        self._simpan(catatan)
        if lama != baru:
            self._catat("STATE", "info", {"dari": lama, "ke": baru,
                                          "catatan": catatan})
        self.siar_status()
        log.info("State %s -> %s (%s)", lama, baru, catatan)

    def _simpan(self, catatan=None):
        baris = db.session.get(LockerState, 1)
        if baris is None:
            baris = LockerState(id=1)
            db.session.add(baris)
        baris.state = self.state
        baris.since = self.sejak
        baris.active_deposit_id = self.deposit_id
        baris.reject_count = self.reject_count
        baris.auto_unlock_count = self.auto_unlock_count
        baris.baseline_cm = self.baseline_cm
        baris.analysis_token = self.analysis_token
        if catatan is not None:
            baris.catatan = catatan[:255]
        self._cache_ringkasan = None

    def _catat(self, tipe, severity, payload, foto=None):
        self._cache_ringkasan = None
        db.session.add(Event(type=tipe, severity=severity,
                             payload=json.dumps(payload, default=str,
                                                ensure_ascii=False),
                             photo_path=foto, created_at=sekarang()))
        self.bus.siar({"tipe": "event", "data": {
            "type": tipe, "severity": severity, "payload": payload}})

    def _ke_admin(self, judul, detail=None):
        self._catat("PERHATIAN", "warning", {"judul": judul, "detail": detail or {}})
        self.notifier.anomali(judul, json.dumps(detail or {}, default=str))
