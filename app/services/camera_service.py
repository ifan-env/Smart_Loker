"""Ambil frame dari webcam dalam loker, atau dari gambar contoh saat mock.

Kamera dibuka sekali dan dipakai ulang (membuka/menutup VideoCapture tiap
frame lambat dan sering gagal di Windows). Akses dilindungi lock karena
analysis worker dan route admin bisa mengambil frame bersamaan.
"""
from __future__ import annotations

import logging
import random
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class CameraService:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mock = cfg.CAMERA_MODE == "mock"
        self._cap = None
        # RLock: _frame_real() boleh memanggil tutup() saat lock masih dipegang.
        self._kunci = threading.RLock()
        self._kelas_paksa = None       # dipakai skenario mock
        self._folder_mock = Path(cfg.FRAME_MOCK_DIR)

    # ---------- mode mock ----------
    def pilih_kelas(self, kelas: str | None):
        """Skenario mock menentukan gambar mana yang akan 'terlihat' kamera."""
        self._kelas_paksa = kelas

    def _frame_mock(self) -> Image.Image:
        if self._kelas_paksa:
            folder = self._folder_mock / self._kelas_paksa
            if folder.is_dir():
                gambar = sorted(p for p in folder.iterdir() if p.suffix.lower() in
                                {".jpg", ".jpeg", ".png", ".bmp", ".webp"})
                if gambar:
                    return Image.open(random.choice(gambar)).convert("RGB")
        # Tidak ada contoh gambar: buat bidang abu-abu bertanda, supaya
        # aplikasi tetap jalan (hasil SVM-nya tidak bermakna, itu wajar).
        img = Image.new("RGB", (640, 480), (70, 70, 70))
        d = ImageDraw.Draw(img)
        d.rectangle([80, 80, 560, 400], outline=(200, 200, 200), width=3)
        d.text((100, 100), "MOCK FRAME", fill=(255, 255, 255))
        return img

    # ---------- mode real ----------
    @staticmethod
    def daftar_kamera() -> list[str]:
        """Nama kamera DirectShow, urutannya sama dengan index VideoCapture."""
        try:
            from pygrabber.dshow_graph import FilterGraph
            return FilterGraph().get_input_devices()
        except Exception:
            log.debug("Daftar nama kamera tidak tersedia (pygrabber)", exc_info=True)
            return []

    def _index_kamera(self) -> tuple[int, str]:
        """Tentukan index kamera; nama lebih stabil daripada nomor index.

        Index DirectShow bergeser saat kamera virtual (DroidCam/OBS) aktif,
        sehingga menulis index di .env mudah salah. Kalau CAMERA_NAMA diisi,
        perangkat dicari lewat namanya.
        """
        diminta = (self.cfg.CAMERA_NAMA or "").strip()
        if not diminta:
            return int(self.cfg.CAMERA_INDEX), ""
        daftar = self.daftar_kamera()
        if not daftar:
            log.warning("Daftar nama kamera tidak tersedia (pygrabber belum "
                        "terpasang); memakai CAMERA_INDEX=%s",
                        self.cfg.CAMERA_INDEX)
            return int(self.cfg.CAMERA_INDEX), ""
        for i, nama in enumerate(daftar):
            if diminta.lower() in nama.strip().lower():
                return i, nama.strip()
        raise CameraError(
            f"Kamera bernama '{diminta}' tidak ditemukan. Yang terpasang: "
            + (", ".join(n.strip() for n in daftar) or "tidak ada")
            + ". Jalankan python scripts/cek_kamera.py"
        )

    @staticmethod
    def _buka_perangkat(index):
        import cv2

        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)      # fallback backend bawaan
        return cap

    @staticmethod
    def _siapkan(cap, mjpg: bool, lebar, tinggi):
        """Terapkan format lalu baca frame. Kembalikan (ok, lebar, tinggi).

        Sebagian webcam menolak MJPG/720p dan malah berhenti mengirim frame
        (get() jadi -1, read() gagal). Karena itu hasilnya divalidasi dengan
        membaca frame sungguhan, bukan hanya percaya nilai properti.
        """
        import cv2

        if mjpg:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if lebar and tinggi:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, lebar)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, tinggi)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Pemanasan: beri waktu auto-exposure/white balance/fokus mengunci.
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                return True, frame.shape[1], frame.shape[0]
        return False, 0, 0

    def _buka(self):
        index, nama = self._index_kamera()
        minta = (self.cfg.CAMERA_LEBAR, self.cfg.CAMERA_TINGGI)
        # Urutan percobaan: sesuai .env -> MJPG 640x480 -> apa adanya.
        percobaan = [(True, *minta), (True, 640, 480), (False, None, None)]
        cap = None
        for mjpg, lebar_minta, tinggi_minta in percobaan:
            cap = self._buka_perangkat(index)
            if not cap.isOpened():
                cap.release()
                cap = None
                continue
            ok, lebar, tinggi = self._siapkan(cap, mjpg, lebar_minta, tinggi_minta)
            if ok:
                break
            cap.release()
            cap = None

        if cap is None:
            raise CameraError(
                f"Kamera {nama or f'index {index}'} tidak bisa dibuka. Cek kabel "
                f"USB, tutup aplikasi lain (Camera/Zoom/Teams), lalu jalankan "
                f"python scripts/cek_kamera.py"
            )
        self._cap = cap
        self._index_aktif, self._nama_aktif = index, nama
        log.info("Kamera %s (index %s) terbuka (%sx%s)",
                 nama or "tanpa nama", index, lebar, tinggi)
        if lebar < minta[0] or tinggi < minta[1]:
            log.warning("Kamera hanya memberi %sx%s, diminta %sx%s",
                        lebar, tinggi, *minta)

    def _frame_real(self) -> Image.Image:
        import cv2

        if self._cap is None:
            self._buka()
        # Buang beberapa frame lama supaya yang dipakai adalah gambar terkini.
        for _ in range(3):
            self._cap.grab()
        # Ambil beberapa frame, pakai yang paling tajam (variance of Laplacian)
        # supaya blur gerak/autofokus tidak langsung merusak OCR.
        terbaik, skor_terbaik = None, -1.0
        for _ in range(5):
            ok, frame = self._cap.read()
            if not ok or frame is None:
                continue
            abu = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            skor = cv2.Laplacian(abu, cv2.CV_64F).var()
            if skor > skor_terbaik:
                terbaik, skor_terbaik = frame, skor
        if terbaik is None:
            self.tutup()
            raise CameraError("Gagal membaca frame dari kamera (kabel/port?).")
        return Image.fromarray(cv2.cvtColor(terbaik, cv2.COLOR_BGR2RGB))

    # ---------- API ----------
    def ambil(self) -> Image.Image:
        with self._kunci:
            if self.mock:
                return self._frame_mock()
            return self._frame_real()

    def cek(self) -> dict:
        """Cek cepat apakah kamera bisa dipakai (tombol admin)."""
        try:
            img = self.ambil()
            nama = getattr(self, "_nama_aktif", "") or ""
            if not nama:
                daftar = self.daftar_kamera()
                index = getattr(self, "_index_aktif", int(self.cfg.CAMERA_INDEX))
                if 0 <= index < len(daftar):
                    nama = daftar[index].strip()
            return {"ok": True, "mode": "mock" if self.mock else "real",
                    "perangkat": nama or f"index {getattr(self, '_index_aktif', self.cfg.CAMERA_INDEX)}",
                    "ukuran": f"{img.width}x{img.height}"}
        except Exception as exc:
            return {"ok": False, "mode": "mock" if self.mock else "real", "error": str(exc)}

    def tutup(self):
        with self._kunci:
            if self._cap is not None:
                try:
                    self._cap.release()
                finally:
                    self._cap = None
