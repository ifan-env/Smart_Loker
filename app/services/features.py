"""Ekstraksi fitur untuk model SVM.

PENTING: dua fungsi di bawah adalah SALINAN PERSIS dari `vision/svm_epoch.py`
(baris 53-79). Jangan diubah, jangan "dirapikan", jangan diganti parameternya.
Model `model_svm_epoch.pkl` dilatih dengan urutan operasi yang sama; satu
perubahan kecil membuat 1.812 fitur tidak lagi cocok dan model salah tebak
tanpa error apa pun.
"""
import cv2
import numpy as np
from PIL import Image
from skimage.feature import hog


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
