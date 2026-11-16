"""Uji model SVM pada seluruh folder dataset dan cetak tabel untuk laporan.

Pakai:
    python scripts/uji_model_folder.py
    python scripts/uji_model_folder.py --folder "D:\\...\\dataset_latih"

Keluaran: akurasi, confusion matrix, presisi/recall/F1 per kelas, dan
kesalahan yang paling sering (berguna untuk bab pembahasan).
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.config import Config                       # noqa: E402
from app.services import svm_service                 # noqa: E402

EKSTENSI = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def utama():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=str(
        AKAR.parents[1] / "Sistem Cerdas" / "Paket" / "projek-semester5" / "dataset_latih"))
    ap.add_argument("--batas", type=int, default=0, help="0 = semua")
    a = ap.parse_args()

    folder = Path(a.folder)
    if not folder.is_dir():
        print(f"Folder tidak ditemukan: {folder}")
        print("Isi --folder dengan lokasi dataset_latih Anda.")
        return 1

    svm_service.muat(Config.MODEL_PATH, Config.JUMLAH_FITUR, Config.SVM_MIN_MARGIN)
    from PIL import Image

    kelas_benar = sorted(p.name for p in folder.iterdir() if p.is_dir())
    benar, total = 0, 0
    matriks = {k: Counter() for k in kelas_benar}
    margin = []
    salah = []

    for kelas in kelas_benar:
        berkas = sorted(p for p in (folder / kelas).iterdir() if p.suffix.lower() in EKSTENSI)
        if a.batas:
            berkas = berkas[: a.batas]
        for jalur in berkas:
            try:
                img = Image.open(jalur).convert("RGB")
                hasil = svm_service.klasifikasi(img)
            except Exception as exc:
                print(f"  gagal {jalur.name}: {exc}")
                continue
            total += 1
            matriks[kelas][hasil["kelas"]] += 1
            margin.append(hasil["margin"])
            if hasil["kelas"] == kelas:
                benar += 1
            else:
                salah.append((jalur.name, kelas, hasil["kelas"], hasil["margin"]))
        print(f"{kelas}: {len(berkas)} gambar diproses")

    if not total:
        print("Tidak ada gambar yang bisa diuji.")
        return 1

    lebar = max(len(k) for k in kelas_benar) + 2
    print("\n=== Confusion matrix (baris = sebenarnya, kolom = prediksi) ===")
    print(" " * lebar + "".join(f"{k:>16}" for k in kelas_benar))
    for k in kelas_benar:
        baris = "".join(f"{matriks[k][p]:>16}" for p in kelas_benar)
        print(f"{k:<{lebar}}" + baris)

    print("\n=== Metrik per kelas ===")
    print(f"{'kelas':<{lebar}}{'presisi':>10}{'recall':>10}{'f1':>10}{'n':>8}")
    for k in kelas_benar:
        tp = matriks[k][k]
        n = sum(matriks[k].values())
        diprediksi = sum(matriks[b][k] for b in kelas_benar)
        presisi = tp / diprediksi if diprediksi else 0
        recall = tp / n if n else 0
        f1 = 2 * presisi * recall / (presisi + recall) if presisi + recall else 0
        print(f"{k:<{lebar}}{presisi:>10.3f}{recall:>10.3f}{f1:>10.3f}{n:>8}")

    akurasi = benar / total
    rata_margin = sum(margin) / len(margin)
    print(f"\nAkurasi total : {akurasi:.3f}  ({benar}/{total})")
    print(f"Margin rata2  : {rata_margin:.3f}  (min {min(margin):.3f}, max {max(margin):.3f})")
    if salah:
        print(f"\n{len(salah)} kesalahan, contoh 10 pertama:")
        for nama, sebenarnya, tebak, m in salah[:10]:
            print(f"  {nama:<28} {sebenarnya} -> {tebak} (margin {m:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(utama())
