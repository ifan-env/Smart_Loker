"""Pencocokan nomor resi dan nama penerima (bukan ML — hanya string matching)."""
from __future__ import annotations

from rapidfuzz import fuzz

# Kebingungan khas OCR: huruf yang mirip angka. Dipetakan ke satu bentuk
# kanonik, diterapkan pada KEDUA sisi perbandingan supaya tetap konsisten.
_PETA_KANONIK = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "J": "1",
    "S": "5", "B": "8", "Z": "2",
    "G": "6", "T": "7",
})


def normalisasi(teks: str) -> str:
    """Uppercase + buang non-alfanumerik. Dipakai untuk kunci simpan/cari."""
    if not teks:
        return ""
    return "".join(ch for ch in teks.upper() if ch.isalnum())


def kanonik(teks: str) -> str:
    """Bentuk tahan salah baca OCR (O/0, I/1, S/5, B/8, ...)."""
    return normalisasi(teks).translate(_PETA_KANONIK)


def jarak_edit(a: str, b: str) -> int:
    """Levenshtein distance sederhana (tanpa dependensi luar)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    sebelumnya = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        sekarang = [i]
        for j, cb in enumerate(b, 1):
            sekarang.append(min(
                sebelumnya[j] + 1,
                sekarang[j - 1] + 1,
                sebelumnya[j - 1] + (ca != cb),
            ))
        sebelumnya = sekarang
    return sebelumnya[-1]


def resi_cocok(baca: str, terdaftar: str, jarak_maks: int = 2) -> tuple[bool, float]:
    """(cocok, skor 0-100). Toleran terhadap salah baca OCR."""
    a, b = kanonik(baca), kanonik(terdaftar)
    if not a or not b:
        return False, 0.0
    if a == b:
        return True, 100.0
    # Resi tersimpan muncul utuh di dalam hasil bacaan (OCR menambah teks liar).
    if len(b) >= 8 and b in a:
        return True, 95.0
    d = jarak_edit(a, b)
    batas = jarak_maks + max(0, (len(b) - 12) // 4)  # resi panjang lebih longgar
    if d <= batas:
        return True, round(100.0 * (1 - d / max(len(a), len(b))), 1)
    return False, round(float(fuzz.ratio(a, b)), 1)


def cocok_resi(baca: str, kandidat, jarak_maks: int = 2):
    """Cari satu parcel dari daftar kandidat. -> (parcel|None, skor, metode)."""
    terbaik, skor_terbaik = None, 0.0
    for parcel in kandidat:
        for nilai in (parcel.tracking_number, getattr(parcel, "tracking_norm", None)):
            if not nilai:
                continue
            ok, skor = resi_cocok(baca, nilai, jarak_maks)
            if ok and skor > skor_terbaik:
                terbaik, skor_terbaik = parcel, skor
    return terbaik, skor_terbaik, ("resi" if terbaik else None)


def cocok_nama(baca: str, warga, min_rasio: float = 85.0, gap: float = 5.0):
    """Cari penghuni berdasarkan nama (fuzzy).

    -> (resident|None, skor, ambigu)
    `ambigu=True` bila dua kandidat teratas skornya berdekatan: lebih baik
    diserahkan ke admin daripada salah orang.
    """
    if not baca or not warga:
        return None, 0.0, False

    nilai = []
    baca_upper = baca.upper()
    for r in warga:
        nama = (r.nama or "").upper()
        skor = max(
            fuzz.WRatio(baca_upper, nama),
            fuzz.partial_ratio(baca_upper, nama),
        )
        nilai.append((skor, r))
    nilai.sort(key=lambda t: t[0], reverse=True)

    skor1, r1 = nilai[0]
    if skor1 < min_rasio:
        return None, round(float(skor1), 1), False
    if len(nilai) > 1:
        skor2 = nilai[1][0]
        if skor1 - skor2 < gap:
            return None, round(float(skor1), 1), True
    return r1, round(float(skor1), 1), False
