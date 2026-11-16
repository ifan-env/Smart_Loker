"""Layanan klasifikasi paket (SVM).

Model dimuat sekali saat start (singleton). Format yang didukung:
  1. dict bundle dari `svm_epoch.py`
     {"scaler", "nystroem", "model", "kelas", "jumlah_fitur", "epoch_terbaik"}
  2. pipeline/sklearn estimator mentah (model utama SVC) — untuk kompatibilitas.
"""
from __future__ import annotations

import logging

import joblib
import numpy as np

from .features import fitur_gambar

log = logging.getLogger(__name__)

_bundle = None
_meta = {}


class SvmError(RuntimeError):
    """Model tidak bisa dipakai; lebih baik gagal di awal daripada salah tebak."""


def muat(path, jumlah_fitur_diharapkan=1812, margin_min=0.30):
    """Muat model sekali. Aman dipanggil berkali-kali (idempotent)."""
    global _bundle, _meta
    if _bundle is not None:
        return _meta

    import sklearn

    bundle = joblib.load(path)

    if isinstance(bundle, dict):
        wajib = {"scaler", "nystroem", "model"}
        hilang = wajib - set(bundle)
        if hilang:
            raise SvmError(f"Bundle model tidak lengkap, kurang: {sorted(hilang)}")
        model = bundle["model"]
        kelas = [str(k) for k in bundle.get("kelas", getattr(model, "classes_", []))]
        jumlah = int(bundle.get("jumlah_fitur", jumlah_fitur_diharapkan))
        epoch = bundle.get("epoch_terbaik")
        jenis = "bundle-epoch"
    else:
        model = bundle
        kelas = [str(k) for k in getattr(model, "classes_", [])]
        jumlah = jumlah_fitur_diharapkan
        epoch = None
        jenis = "estimator"

    if jumlah != jumlah_fitur_diharapkan:
        raise SvmError(
            f"Model dilatih dengan {jumlah} fitur, kode menghasilkan "
            f"{jumlah_fitur_diharapkan}. Ekstraksi fitur tidak cocok."
        )

    _bundle = {
        "jenis": jenis,
        "model": model,
        "scaler": bundle.get("scaler") if isinstance(bundle, dict) else None,
        "nystroem": bundle.get("nystroem") if isinstance(bundle, dict) else None,
        "kelas": kelas,
        "jumlah_fitur": jumlah,
    }
    _meta = {
        "jenis": jenis,
        "kelas": kelas,
        "jumlah_fitur": jumlah,
        "epoch_terbaik": epoch,
        "sklearn": sklearn.__version__,
        "path": str(path),
        "margin_min": margin_min,
    }
    log.info(
        "Model SVM dimuat: %s, kelas=%s, fitur=%s, sklearn=%s, epoch=%s",
        path, kelas, jumlah, sklearn.__version__, epoch,
    )
    return _meta


def meta():
    return dict(_meta)


def siap():
    return _bundle is not None


def _skor_per_kelas(model, X):
    """Skor mentah per kelas (SGD hinge / SVC) sebagai dict."""
    try:
        nilai = model.decision_function(X)
    except (AttributeError, NotImplementedError):
        proba = model.predict_proba(X)[0]
        return {k: float(v) for k, v in zip(_bundle["kelas"], proba)}
    nilai = np.atleast_2d(nilai)[0]
    if nilai.size == 1 and len(_bundle["kelas"]) == 2:
        # Biner: satu skor, tanda menentukan kelas.
        s = float(nilai[0])
        return {_bundle["kelas"][0]: -s, _bundle["kelas"][1]: s}
    return {k: float(v) for k, v in zip(_bundle["kelas"], nilai)}


def klasifikasi(img_rgb, margin_min=None):
    """Klasifikasi satu frame PIL.Image.

    Mengembalikan dict: kelas, margin, skor, ragu.
    `ragu=True` bila selisih skor dua kelas teratas di bawah ambang.
    """
    if _bundle is None:
        raise SvmError("Model belum dimuat.")
    if margin_min is None:
        margin_min = _meta.get("margin_min", 0.30)

    X = fitur_gambar(img_rgb).reshape(1, -1)
    if X.shape[1] != _bundle["jumlah_fitur"]:
        raise SvmError(f"Fitur {X.shape[1]} != {_bundle['jumlah_fitur']}")

    if _bundle["scaler"] is not None:
        Xg = _bundle["scaler"].transform(X)
        Xg = _bundle["nystroem"].transform(Xg)
        label = str(_bundle["model"].predict(Xg)[0])
        skor = _skor_per_kelas(_bundle["model"], Xg)
    else:
        label = str(_bundle["model"].predict(X)[0])
        skor = _skor_per_kelas(_bundle["model"], X)

    urut = sorted(skor.items(), key=lambda kv: kv[1], reverse=True)
    margin = float(urut[0][1] - urut[1][1]) if len(urut) > 1 else float("inf")
    return {
        "kelas": label,
        "margin": round(margin, 4),
        "skor": {k: round(v, 4) for k, v in skor.items()},
        "ragu": margin < margin_min,
    }
