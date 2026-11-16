"""Uji pendaftaran kartu RFID: sesi tap di form penghuni dan simpan lewat form."""
import pytest

from app.models import Event, Parcel, Resident, db, sekarang
from app.services import locker_service as L


@pytest.fixture
def locker(app):
    with app.app_context():
        svc = app.extensions["locker"]
        svc._pulihkan()
        svc.terkirim = []
        svc.serial.send = lambda cmd: svc.terkirim.append(cmd) or True
        yield svc


@pytest.fixture
def admin(client):
    client.post("/login", data={"kamar": "admin", "password": "admin123"})
    return client


def _warga(nomor="A1"):
    return Resident.query.filter_by(nomor_kamar=nomor).first()


def test_normalisasi_uid_menerima_separator():
    from app.routes.admin import _normalisasi_uid
    assert _normalisasi_uid("  a1:b2:c3:d4 ") == "A1B2C3D4"
    assert _normalisasi_uid("a1-b2-c3-d4") == "A1B2C3D4"
    assert _normalisasi_uid("   ") is None


# ------------------------------------------------------------ sesi tap
def test_mulai_tanpa_penghuni_untuk_tambah_baru(locker, app):
    """Penghuni belum ada saat menambah, jadi sesi harus boleh tanpa id."""
    with app.app_context():
        hasil = locker.mulai_daftar_kartu(None, 60)
        assert hasil["ok"], hasil
        assert hasil["data"]["token"]
        info = locker.info_daftar_kartu()
        assert info["resident_id"] is None
        assert info["uid"] is None


def test_tap_selama_sesi_tidak_membuka_loker(locker, app):
    with app.app_context():
        warga = _warga("A1")
        db.session.add(Parcel(resident_id=warga.id, tracking_number="SPXD",
                              tracking_norm="SPXD", status="tersimpan",
                              stored_at=sekarang()))
        db.session.commit()
        locker.state = L.TERSIMPAN
        locker.mulai_daftar_kartu(None, 60)

        locker._tangani("RFID", {"uid": warga.rfid_uid})
        assert locker.state == L.TERSIMPAN, "loker tidak boleh terbuka"
        assert not any(c.startswith("UNLOCK") for c in locker.terkirim)
        assert locker.info_daftar_kartu()["uid"] == warga.rfid_uid


def test_tap_melaporkan_pemilik_kartu(locker, app):
    with app.app_context():
        a1 = _warga("A1")
        locker.mulai_daftar_kartu(None, 60)
        locker._tangani("RFID", {"uid": a1.rfid_uid})
        pemilik = locker.info_daftar_kartu()["pemilik"]
        assert pemilik["nama"] == a1.nama
        assert pemilik["kamar"] == a1.nomor_kamar


def test_tap_kartu_bebas_tanpa_pemilik(locker, app):
    with app.app_context():
        locker.mulai_daftar_kartu(None, 60)
        locker._tangani("RFID", {"uid": "DEADBEEF"})
        info = locker.info_daftar_kartu()
        assert info["uid"] == "DEADBEEF"
        assert info["pemilik"] is None


def test_tap_setelah_sesi_selesai_kembali_normal(locker, app):
    with app.app_context():
        warga = _warga("A1")
        locker.mulai_daftar_kartu(None, 60)
        locker.batal_daftar_kartu()
        assert locker.info_daftar_kartu() is None

        db.session.add(Parcel(resident_id=warga.id, tracking_number="SPXE",
                              tracking_norm="SPXE", status="tersimpan",
                              stored_at=sekarang()))
        db.session.commit()
        locker.state = L.TERSIMPAN
        locker._tangani("RFID", {"uid": warga.rfid_uid.lower()})
        assert locker.state == L.PENGAMBILAN


def test_status_memuat_sesi_tap(locker, app):
    with app.app_context():
        locker.mulai_daftar_kartu(None, 30)
        st = locker.status()
    assert st["daftar_kartu"]["sisa"] > 0
    assert st["daftar_kartu"]["token"]


# ------------------------------------------------------------ form
def test_tambah_penghuni_wajib_ada_kartu(admin, app):
    r = admin.post("/admin/kamar", data={"nama": "Candra", "nomor_kamar": "C1",
                                         "password": "candra123", "rfid_uid": ""},
                   follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Resident.query.filter_by(nomor_kamar="C1").first() is None


def test_tambah_penghuni_dengan_uid_hasil_tap(admin, app):
    admin.post("/admin/kamar", data={"nama": "Candra", "nomor_kamar": "C1",
                                     "password": "candra123",
                                     "rfid_uid": "ca:fe:ba:be"},
               follow_redirects=True)
    with app.app_context():
        w = Resident.query.filter_by(nomor_kamar="C1").first()
        assert w is not None and w.rfid_uid == "CAFEBABE"


def test_tambah_penghuni_menolak_uid_dipakai_lain(admin, app):
    with app.app_context():
        uid_a1 = _warga("A1").rfid_uid
    admin.post("/admin/kamar", data={"nama": "Candra", "nomor_kamar": "C1",
                                     "password": "candra123",
                                     "rfid_uid": uid_a1},
               follow_redirects=True)
    with app.app_context():
        assert Resident.query.filter_by(nomor_kamar="C1").first() is None


def test_halaman_menampilkan_form_edit(admin, app):
    with app.app_context():
        rid = _warga("A1").id
    r = admin.get(f"/admin/kamar?edit={rid}")
    t = r.data.decode()
    assert r.status_code == 200
    assert "Edit penghuni" in t
    assert 'name="resident_id" value="' + str(rid) + '"' in t


def test_edit_penghuni_ganti_nama_kamar_dan_kartu(admin, app):
    with app.app_context():
        w = _warga("A2")
        rid, uid_lama = w.id, w.rfid_uid
    admin.post("/admin/kamar", data={"resident_id": str(rid),
                                     "nama": "Siti Aminah Baru",
                                     "nomor_kamar": "A2-B",
                                     "rfid_uid": "AABBCCDD"},
               follow_redirects=True)
    with app.app_context():
        w = db.session.get(Resident, rid)
        assert w.nama == "Siti Aminah Baru"
        assert w.nomor_kamar == "A2-B"
        assert w.rfid_uid == "AABBCCDD"
        assert w.rfid_uid != uid_lama
        assert Event.query.filter_by(type="PENGHUNI_DIEDIT").count() == 1


def test_edit_penghuni_tidak_mengubah_password(admin, app):
    with app.app_context():
        w = _warga("A1")
        rid, hash_lama = w.id, w.password_hash
    admin.post("/admin/kamar", data={"resident_id": str(rid),
                                     "nama": "Budi Santoso",
                                     "nomor_kamar": "A1",
                                     "rfid_uid": "12345678"},
               follow_redirects=True)
    with app.app_context():
        assert db.session.get(Resident, rid).password_hash == hash_lama


def test_edit_menolak_kamar_dipakai_penghuni_lain(admin, app):
    with app.app_context():
        rid = _warga("A2").id
    admin.post("/admin/kamar", data={"resident_id": str(rid), "nama": "Siti",
                                     "nomor_kamar": "A1", "rfid_uid": "99998888"},
               follow_redirects=True)
    with app.app_context():
        assert db.session.get(Resident, rid).nomor_kamar == "A2"


def test_edit_menolak_kartu_milik_penghuni_lain(admin, app):
    with app.app_context():
        uid_a1 = _warga("A1").rfid_uid
        rid = _warga("A2").id
    admin.post("/admin/kamar", data={"resident_id": str(rid), "nama": "Siti",
                                     "nomor_kamar": "A2", "rfid_uid": uid_a1},
               follow_redirects=True)
    with app.app_context():
        assert db.session.get(Resident, rid).rfid_uid != uid_a1


def test_edit_boleh_pertahankan_uid_sendiri(admin, app):
    """Menyimpan tanpa ganti kartu tidak boleh dianggap bentrok."""
    with app.app_context():
        w = _warga("A1")
        rid, uid = w.id, w.rfid_uid
    r = admin.post("/admin/kamar", data={"resident_id": str(rid),
                                         "nama": "Budi Santoso",
                                         "nomor_kamar": "A1",
                                         "rfid_uid": uid},
                   follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Resident, rid).rfid_uid == uid


def test_admin_lepas_kartu(admin, app):
    with app.app_context():
        rid = _warga("A1").id
    r = admin.post(f"/admin/kamar/{rid}/kartu/hapus", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Resident, rid).rfid_uid is None


# ------------------------------------------------------------ API
def test_api_status_hanya_untuk_token_sesi_sendiri(admin, app):
    with app.app_context():
        L_res = app.extensions["locker"].mulai_daftar_kartu(None, 60)
        token = L_res["data"]["token"]
    j = admin.get("/api/admin/kartu/status?token=" + token).get_json()
    assert j["ok"] and j["data"]["token"] == token
    j2 = admin.get("/api/admin/kartu/status?token=token-lain").get_json()
    assert j2["ok"] and j2["data"] is None


def test_api_mulai_tanpa_resident_id(admin):
    r = admin.post("/api/admin/kartu/mulai", json={"detik": 60})
    j = r.get_json()
    assert j["ok"], j
    assert j["data"]["token"]


def test_api_kartu_butuh_admin(client):
    assert client.get("/api/admin/kartu/status").status_code in (302, 401, 403)
    assert client.post("/api/admin/kartu/mulai", json={}).status_code in (302, 401, 403)
