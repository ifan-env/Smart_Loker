"""Uji model SVM dengan gambar asli dari dataset proyek."""
from pathlib import Path

import pytest
from PIL import Image

from app.services import svm_service

FRAMES = Path(__file__).parent / "frames"
KELAS_SAH = {"ada_resi", "bukanresi_m", "tidak_ada_resi"}


@pytest.fixture(scope="module")
def model():
    from app.config import Config
    if not Path(Config.MODEL_PATH).is_file():
        pytest.skip("model_svm_epoch.pkl tidak ditemukan")
    svm_service.muat(Config.MODEL_PATH, Config.JUMLAH_FITUR, Config.SVM_MIN_MARGIN)
    return svm_service.meta()


def test_meta_model(model):
    assert model["jumlah_fitur"] == 1812
    assert set(model["kelas"]) == KELAS_SAH


def test_klasifikasi_mengembalikan_kelas_sah(model):
    contoh = next((FRAMES / "ada_resi").glob("*.jpg"))
    hasil = svm_service.klasifikasi(Image.open(contoh).convert("RGB"))
    assert hasil["kelas"] in KELAS_SAH
    assert set(hasil["skor"]) == KELAS_SAH
    assert isinstance(hasil["margin"], float)
    assert isinstance(hasil["ragu"], bool)


def test_margin_tinggi_berarti_tidak_ragu(model):
    contoh = next((FRAMES / "tidak_ada_resi").glob("*.jpg"))
    hasil = svm_service.klasifikasi(Image.open(contoh).convert("RGB"))
    if hasil["margin"] >= model["margin_min"]:
        assert hasil["ragu"] is False
    else:
        assert hasil["ragu"] is True
