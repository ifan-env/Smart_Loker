from types import SimpleNamespace

from app.services import matcher


def test_normalisasi_membuang_non_alnum():
    assert matcher.normalisasi(" spx-id 068 895 ") == "SPXID068895"


def test_kanonik_menyamakan_huruf_dan_angka():
    # O/0, I/1, S/5 harus dianggap sama
    assert matcher.kanonik("SPXID068") == matcher.kanonik("5PX1D068")


def test_resi_cocok_persis():
    ok, skor = matcher.resi_cocok("SPXID06889509307A", "SPXID06889509307A")
    assert ok and skor == 100.0


def test_resi_cocok_salah_baca_satu_karakter():
    ok, skor = matcher.resi_cocok("SPXID06889509307B", "SPXID06889509307A")
    assert ok, "satu karakter beda harus ditoleransi"


def test_resi_tidak_cocok_jika_beda_jauh():
    ok, _ = matcher.resi_cocok("JNE1234567890", "SPXID06889509307A")
    assert not ok


def test_resi_tersimpan_muncul_di_dalam_bacaan():
    ok, skor = matcher.resi_cocok("NO RESI SPXID06889509307A TGL 12", "SPXID06889509307A")
    assert ok and skor >= 90


def test_cocok_resi_memilih_parcel_yang_benar():
    a = SimpleNamespace(tracking_number="SPXID06889509307A", tracking_norm="SPXID06889509307A")
    b = SimpleNamespace(tracking_number="JNE999888777666", tracking_norm="JNE999888777666")
    parcel, skor, metode = matcher.cocok_resi("SPXID06889509307A", [a, b])
    assert parcel is a and metode == "resi"


def test_cocok_nama_dan_ambigu():
    warga = [SimpleNamespace(id=1, nama="Budi Santoso"),
             SimpleNamespace(id=2, nama="Siti Aminah")]
    r, skor, ambigu = matcher.cocok_nama("BUDI SANTOSO", warga)
    assert r is warga[0] and not ambigu and skor > 90

    # dua nama yang mirip -> ambigu, jangan menebak
    kembar = [SimpleNamespace(id=1, nama="Ahmad Fauzi"),
              SimpleNamespace(id=2, nama="Ahmad Fauzan")]
    r, skor, ambigu = matcher.cocok_nama("AHMAD FAUZ", kembar, min_rasio=80, gap=5)
    assert ambigu, "nama mirip harus dianggap ambigu"


def test_cocok_nama_tidak_ketemu():
    warga = [SimpleNamespace(id=1, nama="Budi Santoso")]
    r, skor, ambigu = matcher.cocok_nama("Zulkifli", warga)
    assert r is None and not ambigu
