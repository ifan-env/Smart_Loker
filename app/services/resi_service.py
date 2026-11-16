"""Pembacaan resi dari gambar kamera dalam loker.

Urutan: barcode/QR dulu (paling andal), lalu OCR teks. Pipeline OCR memakai
`vision/ocr_resi.py` apa adanya — file itu sudah diuji terpisah pada dataset
proyek, jadi tidak ditulis ulang di sini.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

_VISION = Path(__file__).resolve().parents[2] / "vision"
if str(_VISION) not in sys.path:
    sys.path.insert(0, str(_VISION))

_ocr = None
_ocr_error = None


def _muat_ocr():
    """Impor ocr_resi secara lazy: modul itu memanggil Tesseract saat import."""
    global _ocr, _ocr_error
    if _ocr is not None or _ocr_error is not None:
        return _ocr
    try:
        import ocr_resi  # type: ignore
        _ocr = ocr_resi
    except Exception as exc:  # Tesseract tidak terpasang / DLL hilang
        _ocr_error = str(exc)
        log.warning("OCR tidak tersedia: %s", exc)
    return _ocr


# Label nama pada resi: "Penerima:", "Nama: Budi", "Kepada Yth. Budi"
_POLA_NAMA = (
    re.compile(r"(?:PENERIMA|NAMA\s*PENERIMA|KEPADA|NAMA)\s*[:.\-]?\s*([A-Z][A-Z .'\-]{3,40})"),
)


def baca_barcode(img: Image.Image) -> str | None:
    """QR/barcode lewat OpenCV (tanpa dependensi DLL tambahan)."""
    arr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    detektor = cv2.QRCodeDetector()
    for sumber in (arr, cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)):
        try:
            teks, _, _ = detektor.detectAndDecode(sumber)
        except cv2.error:
            continue
        if teks and teks.strip():
            return teks.strip()
    return None


def ambil_nama(teks: str) -> str | None:
    """Ambil nama penerima dari teks resi, kalau ada."""
    if not teks:
        return None
    for pola in _POLA_NAMA:
        for m in pola.finditer(teks.upper()):
            kandidat = re.sub(r"\s+", " ", m.group(1)).strip(" .-")
            if len(kandidat) >= 3:
                return kandidat.title()
    return None


def baca(gambar) -> dict:
    """Baca resi dari satu frame.

    -> {"resi", "nama", "sumber", "teks", "kandidat", "catatan"}
    sumber: "barcode" | "ocr" | None
    """
    if not isinstance(gambar, Image.Image):
        gambar = Image.open(gambar)

    hasil = {
        "resi": None,
        "nama": None,
        "sumber": None,
        "teks": "",
        "kandidat": [],
        "catatan": None,
    }

    kode = baca_barcode(gambar)
    if kode:
        hasil.update(resi=kode, sumber="barcode")

    ocr = _muat_ocr()
    if ocr is None:
        hasil["catatan"] = f"OCR tidak tersedia: {_ocr_error}"
        return hasil

    try:
        teks, kandidat = ocr.baca_resi(gambar)
    except Exception as exc:  # Tesseract error, gambar rusak, dll.
        hasil["catatan"] = f"OCR gagal: {exc}"
        return hasil

    hasil["teks"] = (teks or "")[:4000]
    hasil["kandidat"] = list(kandidat or [])
    hasil["nama"] = ambil_nama(teks or "")
    if not hasil["resi"]:
        if kandidat:
            # Kalau ada barcode tapi tidak terbaca, OCR jadi sumber utama.
            hasil["resi"] = kandidat[0]
            hasil["sumber"] = "ocr"
    return hasil
