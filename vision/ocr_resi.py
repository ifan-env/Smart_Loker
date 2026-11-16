"""ocr_resi.py — pembungkus Tesseract (pytesseract) untuk proyek ini.

Dipakai untuk membaca teks pada foto paket/resi. BUKAN pengganti SVM:
  - SVM  (latih_dari_folder / svm_epoch) menjawab "ada resi / tidak".
  - OCR  (file ini) menjawab "teks / nomor resinya apa".

Strategi baca nomor resi:
  1. gambar diperbesar ke lebar ~1440 px + dipertajam (foto HP 480x640
     sebelumnya gagal total dibaca Tesseract),
  2. OCR mode blok (psm 6), lalu ambil nomor lewat konteks label
     ("No. Resi:", "AWB") dan pola umum,
  3. kalau belum ketemu: coba psm 11, lalu putar 90/180/270 derajat.

Contoh:
  python ocr_resi.py "dataset_latih/ada_resi/dataset_resi (1).jpg"
  python ocr_resi.py --folder dataset_latih/ada_resi --batas 5
"""
import argparse
import os
import re
import shutil

import pytesseract
from PIL import Image, ImageFilter

# Lokasi umum instalasi Tesseract di Windows (dipakai kalau tidak ada di PATH).
KANDIDAT_TESSERACT = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)

LEBAR_TARGET = 1440   # lebar minimal gambar sebelum di-OCR (akurasi > kecepatan)
SKALA_MAKS = 4.0      # jangan memperbesar lebih dari 4x

# Nomor resi bisa berakhiran huruf (contoh SPXID06889509307A) atau angka saja.
POLA_RESI = (
    re.compile(r"\b[A-Z]{2,6}\d{7,}[A-Z]?\b"),
    re.compile(r"\b\d{8,}\b"),
)
# Nomor HP Indonesia — sering ikut terbaca, jangan dianggap nomor resi.
# Awalan huruf ikut ditoleransi karena OCR sering menempelkan kata sebelumnya
# (contoh "BLOK62895186330992").
POLA_HP = re.compile(r"^[A-Z]{0,4}(?:62)?0?8\d{7,}$")
# Tanggal (31-10-2026 -> 31102026) bukan nomor resi.
POLA_TANGGAL = re.compile(r"^(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])(?:19|20)\d{2}$")
# Awalan nomor resi kurir yang umum — dipakai untuk memilih kandidat terbaik.
PREFIX_KURIR = ("SPX", "JD", "JNE", "CGK", "JX", "LP", "MP")
# Ambil token yang menempel pada label resi.
POLA_KONTEKS = re.compile(
    r"(?:NO\.?\s*RESI|NOMOR\s*RESI|NO\.?\s*AWB|AWB|RESI)\s*[:.\-]?\s*"
    r"([A-Z]{0,6}\d{5,}[A-Z]{0,3})"
)


def atur_tesseract():
    """Pastikan pytesseract tahu letak tesseract.exe."""
    if shutil.which("tesseract"):
        return pytesseract.pytesseract.tesseract_cmd
    for jalur in KANDIDAT_TESSERACT:
        if os.path.isfile(jalur):
            pytesseract.pytesseract.tesseract_cmd = jalur
            return jalur
    raise RuntimeError(
        "Tesseract tidak ditemukan. Install dulu:\n"
        "  winget install -e --id UB-Mannheim.TesseractOCR"
    )


atur_tesseract()


def baca_teks(gambar, lang="eng", psm=6):
    """gambar: path file atau PIL.Image -> teks hasil OCR (apa adanya)."""
    if not isinstance(gambar, Image.Image):
        gambar = Image.open(gambar)
    return pytesseract.image_to_string(
        gambar.convert("RGB"), lang=lang, config=f"--psm {psm}"
    ).strip()


# ---------- perbesar + pertajam ------------------------------------------
def perbesar(img, lebar=LEBAR_TARGET):
    """Perbesar gambar kecil dan pertajam, tanpa mengubah gambar aslinya."""
    img = img.convert("RGB")
    if img.width >= lebar:
        return img
    skala = min(SKALA_MAKS, lebar / img.width)
    ukuran = (int(img.width * skala), int(img.height * skala))
    return (
        img.resize(ukuran, Image.LANCZOS)
        .filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=3))
    )


# ---------- ekstraksi nomor resi -----------------------------------------
def _rapikan(teks):
    """Uppercase + gabungkan angka yang terpisah spasi/strip/titik.

    'JD 0342 3554 19' -> 'JD0342355419'
    """
    teks = teks.upper()
    for _ in range(3):
        teks = re.sub(r"(?<=[A-Z0-9])[ \-\.,](?=\d)", "", teks)
    return teks


def _bersihkan(nomor):
    return nomor.strip("|[](){}.,;:/*\\'\" \t")


def _layak(nomor):
    if len(nomor) < 8 or not re.search(r"\d{5,}", nomor):
        return False
    if POLA_HP.match(nomor) or POLA_TANGGAL.match(nomor):
        return False
    return True


def kandidat_resi(teks):
    """Kembalikan [(nomor, dari_konteks_label), ...] tanpa peringkat."""
    rapi = _rapikan(teks or "")
    hasil = []

    def tambah(nomor, konteks):
        nomor = _bersihkan(nomor)
        if _layak(nomor) and nomor not in [n for n, _ in hasil]:
            hasil.append((nomor, konteks))

    for m in POLA_KONTEKS.finditer(rapi):
        tambah(m.group(1), True)

    for pola in POLA_RESI:
        for m in pola.finditer(rapi):
            tambah(m.group(0), False)

    return hasil


def _skor(kandidat):
    nomor, konteks = kandidat
    skor = 25 if konteks else 0
    if nomor.startswith(PREFIX_KURIR):
        skor += 50
    return skor + min(len(nomor), 20)


def _jarak(a, b):
    """Jarak edit (Levenshtein) — untuk mendeteksi varian salah baca OCR."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    sebelumnya = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        sekarang = [i]
        for j, cb in enumerate(b, 1):
            sekarang.append(min(
                sebelumnya[j] + 1,        # hapus
                sekarang[j - 1] + 1,      # sisip
                sebelumnya[j - 1] + (ca != cb),  # ganti
            ))
        sebelumnya = sekarang
    return sebelumnya[-1]


def _mirip(a, b):
    """True kalau dua nomor hanya beda sedikit (varian salah baca OCR).

    Nomor panjang (>= 12 karakter) diberi toleransi 3 edit karena OCR sering
    menghilangkan/mengganti satu-dua karakter.
    """
    batas = 3 if min(len(a), len(b)) >= 12 else 2
    return _jarak(a, b) <= batas


def cari_nomor_resi(teks):
    """Ambil kandidat nomor resi, diurutkan dari yang paling meyakinkan."""
    hasil = sorted(kandidat_resi(teks), key=_skor, reverse=True)

    nomor = []
    for n, _ in hasil:
        if any(_mirip(n, sudah) for sudah in nomor):
            continue
        nomor.append(n)

    # Kalau ada yang berawalan kurir, buang varian OCR tanpa awalan.
    berprefix = [n for n in nomor if n.startswith(PREFIX_KURIR)]
    if berprefix:
        nomor = berprefix

    return nomor[:5]


# ---------- pipeline lengkap ---------------------------------------------
def baca_resi(gambar, lang="eng"):
    """Baca teks + nomor resi dengan beberapa percobaan.

    Kembalikan (teks, daftar_nomor). Teks yang dikembalikan adalah hasil
    percobaan yang berhasil menemukan nomor (atau percobaan pertama).
    """
    if not isinstance(gambar, Image.Image):
        gambar = Image.open(gambar)
    besar = perbesar(gambar)

    teks = baca_teks(besar, lang=lang, psm=6)

    # Simpan hasil tiap percobaan: (teks, kandidat).
    percobaan = [(teks, kandidat_resi(teks))]

    # Kalau belum ada kandidat berawalan kurir, coba mode teks jarang.
    # Hanya kalau teksnya memang menyerupai label — supaya gambar tanpa resi
    # (mis. foto barang) tidak ikut diproses dua kali.
    ada_prefix = any(n.startswith(PREFIX_KURIR) for n, _ in percobaan[0][1])
    mirip_label = len(teks) > 400 or re.search(r"RESI|AWB", teks.upper())
    if not ada_prefix and mirip_label:
        teks_11 = baca_teks(besar, lang=lang, psm=11)
        percobaan.append((teks_11, kandidat_resi(teks_11)))

    semua = [(kandidat, t) for t, daftar in percobaan for kandidat in daftar]
    if semua:
        kandidat_terbaik, teks_terbaik = max(semua, key=lambda kt: _skor(kt[0]))
        return teks_terbaik, cari_nomor_resi(teks_terbaik)

    # Percobaan terakhir: foto miring/terbalik.
    # Utamakan deteksi orientasi (OSD); kalau OSD gagal tapi teksnya banyak,
    # coba ketiga arah putaran.
    sudut = deteksi_sudut(besar)
    if sudut in (90, 180, 270):
        daftar_sudut = [sudut]
    elif len(teks) > 500:
        daftar_sudut = [90, 180, 270]
    else:
        daftar_sudut = []

    for derajat in daftar_sudut:
        putar = besar.rotate(-derajat, expand=True)  # OSD memakai arah putar
        teks_putar = baca_teks(putar, lang=lang, psm=6)
        nomor = cari_nomor_resi(teks_putar)
        if nomor:
            return teks_putar, nomor

    return teks, []


def deteksi_sudut(img):
    """Sudut putar yang disarankan Tesseract (0/90/180/270), atau None."""
    try:
        osd = pytesseract.image_to_osd(img)
    except pytesseract.TesseractError:
        return None
    m = re.search(r"Rotate:\s*(\d+)", osd)
    return int(m.group(1)) % 360 if m else None


def main():
    ap = argparse.ArgumentParser(description="OCR teks/nomor resi dengan Tesseract.")
    ap.add_argument("gambar", nargs="?", help="path satu file gambar")
    ap.add_argument("--folder", help="proses semua gambar di folder ini")
    ap.add_argument("--batas", type=int, default=10, help="maks gambar per folder")
    args = ap.parse_args()

    if args.folder:
        ekstensi = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        nama = sorted(
            n for n in os.listdir(args.folder) if n.lower().endswith(ekstensi)
        )[: args.batas]
        for n in nama:
            teks, nomor = baca_resi(os.path.join(args.folder, n))
            print(f"=== {n} ===")
            print("nomor terdeteksi:", nomor or "-")
            print(teks[:300] or "(teks kosong)")
            print()
        return

    if not args.gambar:
        ap.error("isi salah satu: path gambar, atau --folder")
    teks, nomor = baca_resi(args.gambar)
    print("Nomor resi terdeteksi:", nomor or "-")
    print("--- teks ---")
    print(teks or "(teks kosong)")


if __name__ == "__main__":
    main()
