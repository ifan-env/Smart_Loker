"""Cek kamera yang terpasang dan tentukan CAMERA_INDEX yang benar.

Pakai:
    python scripts/cek_kamera.py            # pindai index 0-5
    python scripts/cek_kamera.py --simpan   # simpan snapshot tiap index

Tutup dulu aplikasi lain yang memakai kamera (Camera, Zoom, Teams, OBS).
"""
import argparse
import sys
from pathlib import Path

import cv2

AKAR = Path(__file__).resolve().parents[1]
KELUARAN = AKAR / "tests" / "frames" / "cek"


def nama_kamera():
    """Nama perangkat DirectShow; urutannya sama dengan index VideoCapture."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return [n.strip() for n in FilterGraph().get_input_devices()]
    except Exception:
        return []


def pindai(maks=6, simpan=False):
    if simpan:
        KELUARAN.mkdir(parents=True, exist_ok=True)

    nama = nama_kamera()
    print("Memindai kamera index 0-%d ...\n" % (maks - 1))
    ditemukan = 0
    for index in range(maks):
        label = nama[index] if index < len(nama) else "?"
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            print(f"  index {index}: -  ({label})")
            cap.release()
            continue

        for _ in range(3):        # buang frame awal yang gelap
            cap.grab()
        ok, frame = cap.read()
        if not ok:
            print(f"  index {index}: terbuka tapi gagal baca frame  ({label})")
            cap.release()
            continue

        lebar = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        tinggi = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"  index {index}: OK  {lebar}x{tinggi} @ {fps:.0f} fps  ({label})")

        if simpan:
            tujuan = KELUARAN / f"index-{index}.jpg"
            cv2.imwrite(str(tujuan), frame)
            print(f"           snapshot -> {tujuan}")

        ditemukan += 1
        cap.release()

    print()
    if ditemukan == 0:
        print("Tidak ada kamera yang bisa dibuka.")
        print("Periksa: kabel USB, izin kamera Windows, aplikasi lain yang memakai kamera.")
        return 1

    print("Buka snapshot (kalau --simpan) dan lihat mana yang mengarah ke dalam loker.")
    print("Lalu isi di .env:  CAMERA_NAMA=<nama perangkat>  atau  CAMERA_INDEX=<index>")
    print("dan  CAMERA_MODE=real")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maks", type=int, default=6)
    ap.add_argument("--simpan", action="store_true")
    a = ap.parse_args()
    sys.exit(pindai(a.maks, a.simpan))
