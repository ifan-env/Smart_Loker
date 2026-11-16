"""Konfigurasi terpusat Smart Parcel Locker.

Semua angka yang bisa berubah ada di sini (atau di `.env`), tidak tersebar
di dalam kode. Lihat `.env.example` untuk daftar variabelnya.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Muat .env kalau ada. Tidak wajib ada; bawaan di bawah sudah cukup untuk
# menjalankan aplikasi dalam mode mock.
load_dotenv(BASE_DIR / ".env")


def _teks(nama, bawaan):
    nilai = os.environ.get(nama)
    if nilai is None or nilai.strip() == "":
        return bawaan
    return nilai.strip()


def _angka(nama, bawaan, tipe=float):
    nilai = os.environ.get(nama)
    if nilai is None or nilai.strip() == "":
        return bawaan
    try:
        return tipe(nilai)
    except (TypeError, ValueError):
        return bawaan


def _jalur(nama, bawaan):
    nilai = os.environ.get(nama)
    return Path(nilai).expanduser() if nilai else bawaan


def _benar(nama, bawaan):
    nilai = os.environ.get(nama)
    if nilai is None or nilai.strip() == "":
        return bawaan
    return nilai.strip().lower() in {"1", "true", "ya", "yes", "on"}


class Config:
    # ---------- umum ----------
    SECRET_KEY = _teks("SECRET_KEY", "dev-jangan-dipakai-di-produksi")
    # Waktu disimpan di DB sebagai UTC; tampilan web memakai zona ini.
    ZONA_WAKTU = _teks("ZONA_WAKTU", "Asia/Jakarta")
    UPLOAD_DIR = _jalur("UPLOAD_DIR", BASE_DIR / "uploads")
    DATA_DIR = BASE_DIR / "data"
    FOTO_RETENSI_HARI = _angka("FOTO_RETENSI_HARI", 30, int)

    # ---------- database ----------
    _db_file = (BASE_DIR / "data" / "locker.db").as_posix()
    SQLALCHEMY_DATABASE_URI = _teks("DATABASE_URL", f"sqlite:///{_db_file}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False, "timeout": 15},
        "pool_pre_ping": True,
    }

    # ---------- model ----------
    MODEL_PATH = _jalur("SVM_MODEL_PATH", BASE_DIR / "models" / "model_svm_epoch.pkl")
    SVM_MIN_MARGIN = _angka("SVM_MIN_MARGIN", 0.30)
    SKLEARN_DIHARAPKAN = _teks("SKLEARN_DIHARAPKAN", "1.9.1")
    JUMLAH_FITUR = 1812

    # ---------- pencocokan ----------
    RESI_JARAK_MAKS = _angka("RESI_JARAK_MAKS", 2, int)
    NAMA_MIN_RASIO = _angka("NAMA_MIN_RASIO", 85.0)
    NAMA_AMBIGU_GAP = _angka("NAMA_AMBIGU_GAP", 5.0)

    # ---------- serial ----------
    SERIAL_MODE = _teks("SERIAL_MODE", "mock").lower()        # real | mock
    SERIAL_PORT = _teks("SERIAL_PORT", "COM21")
    SERIAL_BAUD = _angka("SERIAL_BAUD", 115200, int)
    SERIAL_TIMEOUT = _angka("SERIAL_TIMEOUT", 0.5)
    SERIAL_AUTODETECT_VID = _teks("SERIAL_AUTODETECT_VID", "1A86")

    # ---------- kamera ----------
    CAMERA_MODE = _teks("CAMERA_MODE", "mock").lower()        # real | mock
    CAMERA_INDEX = _angka("CAMERA_INDEX", 0, int)
    # Kalau diisi, kamera dicari berdasarkan nama perangkat (DirectShow),
    # bukan nomor index yang bisa berubah ketika kamera virtual aktif.
    CAMERA_NAMA = _teks("CAMERA_NAMA", "")
    CAMERA_LEBAR = _angka("CAMERA_LEBAR", 1280, int)
    CAMERA_TINGGI = _angka("CAMERA_TINGGI", 720, int)
    FRAME_MOCK_DIR = BASE_DIR / "tests" / "frames"

    # ---------- state machine ----------
    TENANG_MS = _angka("TENANG_MS", 1500, int)                 # jeda paket diam
    BATAS_TOLAK = _angka("BATAS_TOLAK", 3, int)
    ANALYSIS_TIMEOUT_S = _angka("ANALYSIS_TIMEOUT_S", 12, int)
    PINTU_TERBUKA_MAKS_S = _angka("PINTU_TERBUKA_MAKS_S", 120, int)
    AMBIL_MAKS_S = _angka("AMBIL_MAKS_S", 60, int)
    AUTO_UNLOCK_MAKS_PER_JAM = _angka("AUTO_UNLOCK_MAKS_PER_JAM", 3, int)
    AUTO_UNLOCK_JEDA_S = _angka("AUTO_UNLOCK_JEDA_S", 10, int)
    # Kunci pegas/fail-secure: kait selalu terkunci saat solenoid mati, jadi
    # "kosong + terkunci" adalah kondisi normal, bukan anomali.
    KUNCI_FAIL_SECURE = _benar("KUNCI_FAIL_SECURE", False)
    UNLOCK_MS = _angka("UNLOCK_MS", 3000, int)                 # 500-3000 (batas firmware)
    TOMBOL_JEDA_S = _angka("TOMBOL_JEDA_S", 5, int)            # cooldown tombol titip

    # ---------- lampu ----------
    LAMPU_MAKS_DETIK = _angka("LAMPU_MAKS_DETIK", 120, int)

    # ---------- OTP ----------
    OTP_PANJANG = _angka("OTP_PANJANG", 6, int)
    OTP_MENIT = _angka("OTP_MENIT", 30, int)
    OTP_MAKS_PERCOBAAN = _angka("OTP_MAKS_PERCOBAAN", 5, int)

    # ---------- Telegram ----------
    TELEGRAM_TOKEN = _teks("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ADMIN = _teks("TELEGRAM_CHAT_ADMIN", "")

    # ---------- lain ----------
    TESTING = False
    START_SERVICES = True      # dimatikan saat unit test
