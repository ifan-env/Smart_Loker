"""Application factory: merakit DB, layanan, route, dan aset ML."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, current_app, session
from flask_sqlalchemy import SQLAlchemy

from .config import Config
from .models import Resident, db
from .services.bus import Bus
from .services.camera_service import CameraService
from .services.locker_service import LockerService
from .services.notify import Notifier
from .services.serial_service import SerialService
from .services import svm_service

log = logging.getLogger(__name__)


def ke_waktu_lokal(nilai, zona=None):
    """DB menyimpan waktu dalam UTC tanpa tzinfo; ubah ke zona tampilan."""
    if not nilai:
        return None
    if nilai.tzinfo is None:
        nilai = nilai.replace(tzinfo=timezone.utc)
    if zona:
        try:
            return nilai.astimezone(ZoneInfo(zona))
        except (KeyError, ValueError):
            pass
    return nilai.astimezone()


def _siapkan_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )


def _seed():
    """Akun awal supaya aplikasi bisa langsung dipakai setelah instalasi."""
    if Resident.query.count() > 0:
        return
    from werkzeug.security import generate_password_hash

    admin = Resident(
        nama="Pengelola", nomor_kamar="admin",
        password_hash=generate_password_hash("admin123"),
        role="admin",
    )
    warga1 = Resident(
        nama="Budi Santoso", nomor_kamar="A1",
        password_hash=generate_password_hash("budi123"),
        rfid_uid="A1B2C3D4",
    )
    warga2 = Resident(
        nama="Siti Aminah", nomor_kamar="A2",
        password_hash=generate_password_hash("siti123"),
        rfid_uid="11223344",
    )
    db.session.add_all([admin, warga1, warga2])
    db.session.commit()
    log.warning("Akun awal dibuat: admin/admin123, A1/budi123, A2/siti123 "
                "(ganti sebelum dipakai sungguhan)")


def _siapkan_svm(app):
    cfg = app.config
    try:
        svm_service.muat(
            cfg["MODEL_PATH"],
            jumlah_fitur_diharapkan=cfg["JUMLAH_FITUR"],
            margin_min=cfg["SVM_MIN_MARGIN"],
        )
    except Exception:
        # Aplikasi tetap boleh jalan (halaman admin & web), tapi analisis
        # paket akan gagal dengan pesan jelas. Lebih baik daripada crash total.
        log.exception("Model SVM gagal dimuat dari %s", cfg["MODEL_PATH"])


def create_app(config_object=Config):
    _siapkan_logging()

    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)

    with app.app_context():
        app.config["DATA_DIR"].mkdir(parents=True, exist_ok=True)
        app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
        db.create_all()
        _seed()
        _siapkan_svm(app)

    bus = Bus()
    cfg = ConfigProxy(app.config)
    notifier = Notifier(app.config["TELEGRAM_TOKEN"],
                        app.config["TELEGRAM_CHAT_ADMIN"])
    camera = CameraService(cfg)

    locker_holder = {}
    serial = SerialService(
        cfg,
        lambda jenis, payload: locker_holder["locker"].kirim_event(jenis, payload),
    )
    locker = LockerService(app, cfg, serial, camera, notifier, bus)
    locker_holder["locker"] = locker

    app.extensions["bus"] = bus
    app.extensions["notifier"] = notifier
    app.extensions["camera"] = camera
    app.extensions["serial"] = serial
    app.extensions["locker"] = locker

    from .routes.admin import bp_admin
    from .routes.api import bp_api
    from .routes.penghuni import bp_web

    app.register_blueprint(bp_web)
    app.register_blueprint(bp_api)
    app.register_blueprint(bp_admin)

    if app.config.get("START_SERVICES", True):
        serial.mulai()
        locker.mulai()

    @app.context_processor
    def _inject():
        from datetime import datetime
        from .models import Resident as R
        uid = session.get("uid")
        return {
            "pengguna": db.session.get(R, uid) if uid else None,
            "sekarang": datetime.now(),
        }

    def _zona():
        return current_app.config.get("ZONA_WAKTU")

    @app.template_filter("waktu")
    def _waktu(nilai):
        nilai = ke_waktu_lokal(nilai, _zona())
        if not nilai:
            return "-"
        return nilai.strftime("%d-%m-%Y %H:%M")

    @app.template_filter("jam")
    def _jam(nilai, format="%H:%M:%S"):
        nilai = ke_waktu_lokal(nilai, _zona())
        if not nilai:
            return "-"
        return nilai.strftime(format)

    @app.template_filter("ringkas")
    def _ringkas(nilai, batas=90):
        if nilai is None:
            return ""
        teks = nilai
        if isinstance(nilai, str):
            try:
                teks = json.loads(nilai)
            except (ValueError, TypeError):
                teks = nilai
        if isinstance(teks, dict):
            teks = " · ".join(f"{k}={v}" for k, v in teks.items())
        else:
            teks = str(teks)
        teks = " ".join(teks.split())
        return teks[:batas] + ("…" if len(teks) > batas else "")

    @app.template_filter("rapi")
    def _rapi(nilai):
        """'perlu_manual' -> 'Perlu manual'."""
        return str(nilai or "").replace("_", " ").strip().capitalize()

    @app.template_filter("tanggal")
    def _tanggal(nilai):
        """Rabu, 12 November 2026."""
        hari = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        bulan = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
                 "Agustus", "September", "Oktober", "November", "Desember"]
        if not nilai:
            return "-"
        return f"{hari[nilai.weekday()]}, {nilai.day} {bulan[nilai.month - 1]} {nilai.year}"

    @app.template_filter("sensor")
    def _sensor(nilai):
        """Nilai protokol firmware -> kata Indonesia."""
        return {
            "closed": "Tertutup", "open": "Terbuka",
            "locked": "Terkunci", "released": "Terlepas",
            "present": "Ada benda", "empty": "Kosong",
            "on": "Menyala", "off": "Mati",
        }.get(str(nilai or "").lower(), str(nilai or "-"))

    return app


class ConfigProxy:
    """Adaptor: layanan hanya butuh beberapa kunci dari app.config."""

    def __init__(self, cfg):
        self._cfg = cfg

    def __getattr__(self, nama):
        try:
            return self._cfg[nama]
        except KeyError as exc:
            raise AttributeError(nama) from exc
