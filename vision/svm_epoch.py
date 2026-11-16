#!/usr/bin/env python3
"""
SVM BER-EPOCH — pendamping latih_dari_folder.py, BUKAN penggantinya.

Kenapa file ini ada:
  SVM utama proyek (SVC kernel RBF, C=5) dilatih lewat optimasi SMO yang
  berjalan SEKALI sampai konvergen (syarat KKT). Ia TIDAK punya epoch dan
  tidak bisa ditambah epoch — fit() SVC memang tidak punya parameter itu.

  Varian di file ini TETAP SVM (fungsi rugi hinge + margin maksimum, ciri
  khas SVM), tetapi:
    1. kernel RBF dihadirkan lewat APROKSIMASI Nystroem — fitur 1.812
       dipetakan ke 300 fitur baru yang menyerupai ruang kernel RBF, dan
    2. pelatihannya memakai SGD (stochastic gradient descent) yang belajar
       SATU PUTARAN PENUH data latih per epoch, sehingga akurasi dan loss
       berubah tiap epoch dan bisa dilaporkan / digambar kurvanya.

  Jadi kalimat yang JUJUR untuk laporan adalah:
  "Selain SVM RBF standar (SVC), disiapkan pula varian SVM yang dilatih
  secara iteratif per epoch memakai SGD di atas fitur aproksimasi kernel
  RBF (Nystroem); model mencapai akurasi uji terbaik ... pada epoch ke-..."

Pemakaian:
  python svm_epoch.py --data dataset_latih --epochs 50
Keluaran:
  - tabel epoch (loss hinge data latih, akurasi latih, akurasi uji) per epoch
    pada split per kelompok foto asli (seed 42, sama seperti evaluasi utama)
  - kurva_epoch_svm.png   : grafik akurasi per epoch
  - hasil_epoch_svm.txt   : tabel yang sama dalam bentuk teks
  - model_svm_epoch.pkl   : model final dilatih di SELURUH data selama
                            sejumlah epoch terbaik dari percobaan di atas
"""
import argparse
import os

import cv2
import joblib
import numpy as np
from PIL import Image
from skimage.feature import hog
from sklearn.kernel_approximation import Nystroem
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

EKSTENSI = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
SEED = 42


# ---------- preprocessing + fitur: IDENTIK dengan latih_dari_folder.py ----
def prapemrosesan(abu):
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    hasil = clahe.apply(abu)
    return cv2.bilateralFilter(hasil, 9, 75, 75)


def fitur_gambar(path_atau_pil):
    if isinstance(path_atau_pil, Image.Image):
        img = path_atau_pil.convert("RGB")
    else:
        img = Image.open(path_atau_pil).convert("RGB")
    abu = np.asarray(img.convert("L"), dtype=np.uint8)
    olah = prapemrosesan(abu)
    kecil = cv2.resize(olah, (64, 64)).astype(np.float64) / 255.0
    fitur_hog = hog(kecil, orientations=9, pixels_per_cell=(8, 8),
                    cells_per_block=(2, 2), block_norm="L2-Hys",
                    feature_vector=True)
    arr = np.asarray(img.resize((64, 64)), dtype=np.float64)
    hist = []
    for kanal in range(3):
        h, _ = np.histogram(arr[:, :, kanal], bins=16, range=(0, 256))
        hist.extend(h.astype(np.float64))
    hist = np.asarray(hist, dtype=np.float64)
    total = hist.sum()
    if total > 0:
        hist /= total
    return np.concatenate([fitur_hog, hist])


def nama_grup(nama_file, kelas):
    dasar = os.path.splitext(nama_file)[0]
    for suf in ("_r90", "_r180", "_r270", " copy"):
        if dasar.endswith(suf):
            dasar = dasar[: -len(suf)]
            break
    return f"{kelas}/{dasar}"


def muat_dataset(akar):
    jalur, y, grup = [], [], []
    for kelas in sorted(os.listdir(akar)):
        sub = os.path.join(akar, kelas)
        if not os.path.isdir(sub):
            continue
        for nama in sorted(os.listdir(sub)):
            if nama.lower().endswith(EKSTENSI):
                jalur.append(os.path.join(sub, nama))
                y.append(kelas)
                grup.append(nama_grup(nama, kelas))
    X = np.array([fitur_gambar(p) for p in jalur])
    return X, np.array(y), np.array(grup)


# ---------- satu epoch demi satu epoch ------------------------------------
def latih_per_epoch(Z_latih, y_latih, Z_uji, y_uji, jumlah_epoch, kelas):
    sgd = SGDClassifier(loss="hinge", random_state=SEED)
    rng = np.random.default_rng(SEED)
    biner = len(kelas) == 2
    tanda = np.where(y_latih == kelas[1], 1.0, -1.0) if biner else None
    baris = []
    for epoch in range(1, jumlah_epoch + 1):
        urutan = rng.permutation(len(y_latih))  # acak urutan tiap epoch
        sgd.partial_fit(Z_latih[urutan], y_latih[urutan], classes=kelas)
        acc_latih = accuracy_score(y_latih, sgd.predict(Z_latih))
        acc_uji = accuracy_score(y_uji, sgd.predict(Z_uji))
        loss = None
        if biner:
            skor = sgd.decision_function(Z_latih)
            loss = float(np.mean(np.maximum(0.0, 1.0 - tanda * skor)))
        baris.append((epoch, loss, acc_latih, acc_uji))
    return sgd, baris


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="dataset_latih")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--komponen", type=int, default=300,
                    help="jumlah fitur aproksimasi Nystroem")
    ap.add_argument("--out", default="model_svm_epoch.pkl")
    args = ap.parse_args()

    print(f"Memuat dataset dari {args.data} ...")
    X, y, grup = muat_dataset(args.data)
    kelas = sorted(set(y.tolist()))
    print(f"Total {len(y)} gambar, {len(kelas)} kelas: {kelas}, "
          f"{X.shape[1]} fitur, {len(set(grup.tolist()))} foto asli (grup)")
    if len(kelas) < 2:
        raise SystemExit("Butuh minimal 2 kelas berisi foto untuk melatih.")

    sgkf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED)
    idx_latih, idx_uji = next(sgkf.split(X, y, grup))
    tumpang = len(set(grup[idx_latih]) & set(grup[idx_uji]))
    print(f"Split grup: latih {len(idx_latih)} | uji {len(idx_uji)} | "
          f"grup tumpang tindih {tumpang}")

    scaler = StandardScaler().fit(X[idx_latih])
    Xl = scaler.transform(X[idx_latih])
    Xu = scaler.transform(X[idx_uji])
    # gamma 'scale' dihitung dari fitur SETELAH distandardisasi — sama
    # seperti yang diterima SVC di pipeline utama (di sana scaler juga
    # berjalan lebih dulu, sehingga gamma-nya sekitar 1/1812).
    gamma = 1.0 / (X.shape[1] * Xl.var())
    komponen = min(args.komponen, len(idx_latih))
    print(f"gamma (scale, dari fitur terstandarisasi) = {gamma:.6f}; "
          f"Nystroem {komponen} komponen")
    nys = Nystroem(kernel="rbf", gamma=gamma, n_components=komponen,
                   random_state=SEED).fit(Xl)
    Zl, Zu = nys.transform(Xl), nys.transform(Xu)

    print(f"\nMelatih SVM-SGD selama {args.epochs} epoch ...")
    sgd, baris = latih_per_epoch(Zl, y[idx_latih], Zu, y[idx_uji],
                                 args.epochs, np.array(kelas))
    kepala = f"{'epoch':>5} | {'loss hinge':>10} | {'akurasi latih':>13} | {'akurasi uji':>11}"
    garis = [kepala, "-" * len(kepala)]
    for epoch, loss, a_l, a_u in baris:
        lstr = f"{loss:.4f}" if loss is not None else "-"
        garis.append(f"{epoch:>5} | {lstr:>10} | {a_l:>13.3f} | {a_u:>11.3f}")
    tabel = "\n".join(garis)
    print(tabel)

    terbaik = max(baris, key=lambda b: b[3])
    print(f"\nAkurasi uji terbaik {terbaik[3]:.3f} pada epoch ke-{terbaik[0]}")
    cm = confusion_matrix(y[idx_uji], sgd.predict(Zu), labels=kelas)
    print(f"Confusion matrix (baris = sebenarnya {kelas}):\n{cm}")

    svc = SVC(C=5, kernel="rbf", gamma="scale")
    svc.fit(Xl, y[idx_latih])
    print(f"Pembanding satu split yang sama — SVC RBF C=5 (model utama, "
          f"tanpa epoch): akurasi uji {svc.score(Xu, y[idx_uji]):.3f}")

    # model final: seluruh data, dilatih selama epoch terbaik
    scaler_f = StandardScaler().fit(X)
    Xf = scaler_f.transform(X)
    nys_f = Nystroem(kernel="rbf", gamma=gamma,
                     n_components=min(args.komponen, len(y)),
                     random_state=SEED).fit(Xf)
    Zf = nys_f.transform(Xf)
    final = SGDClassifier(loss="hinge", random_state=SEED)
    rng = np.random.default_rng(SEED)
    for _ in range(terbaik[0]):
        urutan = rng.permutation(len(y))
        final.partial_fit(Zf[urutan], y[urutan], classes=np.array(kelas))
    joblib.dump({"scaler": scaler_f, "nystroem": nys_f, "model": final,
                 "kelas": kelas, "jumlah_fitur": int(X.shape[1]),
                 "epoch_terbaik": int(terbaik[0])}, args.out)
    print(f"Model final ({terbaik[0]} epoch, seluruh data) tersimpan: {args.out}")

    with open("hasil_epoch_svm.txt", "w", encoding="utf-8") as f:
        f.write("SVM ber-epoch: SGD (hinge) di atas aproksimasi Nystroem RBF\n")
        f.write(f"Dataset {args.data}: {len(y)} file, {X.shape[1]} fitur, "
                f"split grup latih {len(idx_latih)} / uji {len(idx_uji)}\n\n")
        f.write(tabel + "\n\n")
        f.write(f"Akurasi uji terbaik {terbaik[3]:.3f} pada epoch ke-{terbaik[0]}\n")
        f.write(f"Confusion matrix (kelas {kelas}):\n{cm}\n")
        f.write(f"SVC RBF C=5 pada split sama: {svc.score(Xu, y[idx_uji]):.3f}\n")
    print("Tabel tersimpan: hasil_epoch_svm.txt")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ep = [b[0] for b in baris]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(ep, [b[2] for b in baris], label="Akurasi latih")
        ax.plot(ep, [b[3] for b in baris], label="Akurasi uji")
        ax.axvline(terbaik[0], color="gray", linestyle="--", linewidth=1)
        ax.set_xlabel("Epoch")
        ax.set_ylabel("Akurasi")
        ax.set_ylim(0.4, 1.02)
        ax.grid(alpha=0.3)
        ax.legend()
        ax.set_title("SVM ber-epoch (SGD + aproksimasi RBF Nystroem): "
                     "akurasi per epoch")
        plt.tight_layout()
        plt.savefig("kurva_epoch_svm.png", dpi=150)
        print("Kurva tersimpan: kurva_epoch_svm.png")
    except ImportError:
        print("matplotlib belum terpasang — kurva dilewati, tabel tetap ada.")


if __name__ == "__main__":
    main()
