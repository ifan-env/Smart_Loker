"""Uji state machine tanpa hardware dan tanpa thread (deterministik)."""
import pytest

from app.models import Deposit, Event, Parcel, PickupCode, db, sekarang
from app.services import locker_service as L


@pytest.fixture
def locker(app):
    with app.app_context():
        svc = app.extensions["locker"]
        svc._pulihkan()
        svc.terkirim = []
        svc.serial.send = lambda cmd: svc.terkirim.append(cmd) or True
        yield svc


def atur_sensor(locker, **nilai):
    locker.serial._snapshot.update(nilai)
    return locker.serial._snapshot


def test_object_present_masuk_state_paket(locker):
    atur_sensor(locker, door="open", lock="released", object="empty")
    locker._tangani("OBJECT", {"state": "present"})
    assert locker.state == L.PAKET_MASUK
    assert "LIGHT ON" in locker.terkirim


def test_pintu_tertutup_dan_terkunci_memulai_timer_tenang(locker, monkeypatch):
    dipanggil = []
    monkeypatch.setattr(locker, "_mulai_analisis", lambda: dipanggil.append(1))
    locker.state = L.PAKET_MASUK
    atur_sensor(locker, door="closed", lock="locked", object="present")
    locker._tangani("DOOR", {"state": "closed"})
    assert locker.state == L.MENUNGGU_TENANG
    assert locker._tenang_sampai > 0


def test_pintu_terbuka_lagi_membatalkan_timer(locker):
    locker.state = L.MENUNGGU_TENANG
    locker._tenang_sampai = 999999.0
    atur_sensor(locker, door="open")
    locker._tangani("DOOR", {"state": "open"})
    assert locker.state == L.PAKET_MASUK
    assert locker._tenang_sampai == 0.0


def test_tiga_penolakan_berakhir_di_tunggu_admin(locker, app):
    with app.app_context():
        dep = Deposit(captured_at=sekarang(), attempt_no=1)
        db.session.add(dep)
        db.session.commit()
        dep_id = dep.id

    for i in range(locker.cfg.BATAS_TOLAK - 1):
        dep = db.session.get(Deposit, dep_id)
        locker._tolak(dep, "uji")
        assert locker.state == L.PAKET_MASUK
        assert "UNLOCK 3000" in locker.terkirim

    dep = db.session.get(Deposit, dep_id)
    locker._tolak(dep, "uji")
    assert locker.state == L.TUNGGU_ADMIN
    assert locker.reject_count == locker.cfg.BATAS_TOLAK


def test_auto_unlock_dibatasi_dan_naik_ke_tunggu_admin(locker):
    locker.state = L.IDLE_OPEN
    locker.auto_unlock_count = 0
    locker.auto_unlock_terakhir = 0.0
    atur_sensor(locker, door="closed", lock="locked", object="empty")

    for i in range(locker.cfg.AUTO_UNLOCK_MAKS_PER_JAM):
        locker.auto_unlock_terakhir = 0.0     # lewati jeda
        locker._tick()
        assert locker.auto_unlock_count == i + 1

    locker.auto_unlock_terakhir = 0.0
    locker._tick()
    assert locker.state == L.TUNGGU_ADMIN, "harus berhenti, bukan unlock terus"


def test_rfid_tanpa_paket_tidak_membuka(locker, app):
    from app.models import Resident
    with app.app_context():
        warga = Resident.query.filter_by(nomor_kamar="A1").first()
        assert warga and warga.rfid_uid
        locker.state = L.TERSIMPAN
        locker._tangani("RFID", {"uid": warga.rfid_uid.lower()})
    assert "UNLOCK 3000" not in locker.terkirim


def test_rfid_dengan_paket_membuka_pintu(locker, app):
    from app.models import Resident
    with app.app_context():
        warga = Resident.query.filter_by(nomor_kamar="A1").first()
        db.session.add(Parcel(resident_id=warga.id, tracking_number="SPXID1",
                              tracking_norm="SPXID1", status="tersimpan",
                              stored_at=sekarang()))
        db.session.commit()
        locker.state = L.TERSIMPAN
        locker._tangani("RFID", {"uid": warga.rfid_uid})
    assert locker.state == L.PENGAMBILAN
    assert "UNLOCK 3000" in locker.terkirim


def test_object_empty_saat_pengambilan_menyelesaikan(locker, app):
    with app.app_context():
        from app.models import Resident
        warga = Resident.query.filter_by(nomor_kamar="A1").first()
        p = Parcel(resident_id=warga.id, tracking_number="SPXID2",
                   tracking_norm="SPXID2", status="tersimpan", stored_at=sekarang())
        db.session.add(p)
        db.session.commit()
        locker.state = L.PENGAMBILAN
        locker._tangani("OBJECT", {"state": "empty"})
        assert locker.state == L.DIAMBIL
        assert db.session.get(Parcel, p.id).status == "diambil"


def test_otp_benar_membuka_dan_sekali_pakai(locker, app):
    with app.app_context():
        from app.models import Resident
        warga = Resident.query.filter_by(nomor_kamar="A2").first()
        p = Parcel(resident_id=warga.id, tracking_number="SPXID3",
                   tracking_norm="SPXID3", status="tersimpan", stored_at=sekarang())
        db.session.add(p)
        db.session.commit()
        kode = locker._buat_otp(p)
        db.session.commit()
        assert PickupCode.query.count() >= 1

        ok, pesan = locker.verifikasi_otp(kode)
        assert ok, pesan
        assert locker.state == L.PENGAMBILAN

        locker.state = L.TERSIMPAN
        ok2, _ = locker.verifikasi_otp(kode)
        assert not ok2, "kode tidak boleh bisa dipakai dua kali"


def test_otp_salah_ditolak(locker, app):
    with app.app_context():
        from app.models import Resident
        warga = Resident.query.filter_by(nomor_kamar="A2").first()
        p = Parcel(resident_id=warga.id, tracking_number="SPXID4",
                   tracking_norm="SPXID4", status="tersimpan", stored_at=sekarang())
        db.session.add(p)
        db.session.commit()
        locker._buat_otp(p)
        db.session.commit()
        ok, _ = locker.verifikasi_otp("999999")
        assert not ok


def test_reset_admin_kembali_standby(locker):
    locker.state = L.TUNGGU_ADMIN
    locker.reject_count = 3
    locker.auto_unlock_count = 2
    hasil = locker.aksi_admin("reset")
    assert hasil["ok"]
    assert locker.state == L.IDLE_OPEN
    assert locker.reject_count == 0
    assert locker.auto_unlock_count == 0
    assert "LIGHT OFF" in locker.terkirim


def test_lampu_mencatat_sisa_waktu(locker):
    locker._lampu(True)
    assert 100 < locker.sisa_lampu() <= 120
    assert "LIGHT ON" in locker.terkirim
    locker._lampu(False)
    assert locker.sisa_lampu() == 0

def test_tombol_titip_membuka_saat_kosong(locker):
    locker._pulihkan()
    locker.state = L.IDLE_OPEN
    locker._tombol_terakhir = 0.0
    atur_sensor(locker, door="closed", lock="locked", object="empty")
    locker._tangani("BUTTON", {"state": "press"})
    assert "UNLOCK 3000" in locker.terkirim
    assert "LIGHT ON" in locker.terkirim


def test_tombol_titip_ditolak_saat_ada_paket(locker):
    locker.state = L.TERSIMPAN
    atur_sensor(locker, door="closed", lock="locked", object="present")
    locker._tangani("BUTTON", {"state": "press"})
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)
    assert locker.state == L.TERSIMPAN


def test_tombol_titip_ditolak_saat_pintu_terbuka(locker):
    locker.state = L.IDLE_OPEN
    atur_sensor(locker, door="open", lock="locked", object="empty")
    locker._tangani("BUTTON", {"state": "press"})
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)


def test_fail_secure_tidak_auto_unlock_saat_kosong(locker):
    locker.cfg._cfg["KUNCI_FAIL_SECURE"] = True
    locker.state = L.IDLE_OPEN
    locker.auto_unlock_count = 0
    atur_sensor(locker, door="closed", lock="locked", object="empty")
    for _ in range(5):
        locker.auto_unlock_terakhir = 0.0
        locker._tick()
    assert locker.auto_unlock_count == 0
    assert locker.state == L.IDLE_OPEN
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)


def test_pengambilan_batal_kembali_tersimpan(locker, app):
    from app.models import Resident
    with app.app_context():
        warga = Resident.query.filter_by(nomor_kamar="A1").first()
        p = Parcel(resident_id=warga.id, tracking_number="SPXID9",
                   tracking_norm="SPXID9", status="tersimpan", stored_at=sekarang())
        db.session.add(p)
        db.session.commit()
    locker.state = L.PENGAMBILAN
    locker._pengambilan_sampai = 1.0        # sudah lewat
    atur_sensor(locker, door="closed", lock="locked", object="present")
    locker._tick()
    assert locker.state == L.TERSIMPAN
    assert "PENGAMBILAN_BATAL" in [e.type for e in Event.query.all()]

def test_buka_manual_ditolak_saat_ada_paket_fisik(locker):
    locker.state = L.IDLE_OPEN
    atur_sensor(locker, door="closed", lock="locked", object="present")
    hasil = locker.aksi_admin("unlock")
    assert not hasil["ok"]
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)


def test_buka_manual_ditolak_saat_ada_paket_tersimpan(locker, app):
    from app.models import Resident
    with app.app_context():
        warga = Resident.query.filter_by(nomor_kamar="A1").first()
        db.session.add(Parcel(resident_id=warga.id, tracking_number="SPXBUKA1",
                              tracking_norm="SPXBUKA1", status="tersimpan",
                              stored_at=sekarang()))
        db.session.commit()
    locker.state = L.IDLE_OPEN
    atur_sensor(locker, door="closed", lock="locked", object="empty")
    hasil = locker.aksi_admin("unlock")
    assert not hasil["ok"]
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)


def test_buka_manual_boleh_saat_kosong(locker):
    locker.state = L.IDLE_OPEN
    atur_sensor(locker, door="closed", lock="locked", object="empty")
    hasil = locker.aksi_admin("unlock")
    assert hasil["ok"]
    assert "UNLOCK 3000" in locker.terkirim

def test_buka_paksa_tetap_bisa_saat_ada_paket(locker):
    locker.state = L.TERSIMPAN
    atur_sensor(locker, door="closed", lock="locked", object="present")
    locker.serial.terhubung = True
    hasil = locker.aksi_admin("unlock_paksa")
    assert hasil["ok"] and hasil.get("darurat")
    assert "UNLOCK 3000" in locker.terkirim


def test_buka_paksa_gagal_tanpa_serial(locker):
    locker.state = L.TERSIMPAN
    locker.serial.terhubung = False
    hasil = locker.aksi_admin("unlock_paksa")
    assert not hasil["ok"]
    assert not any(c.startswith("UNLOCK") for c in locker.terkirim)
