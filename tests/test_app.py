"""Smoke test web: login, halaman utama, endpoint status."""
import json

from app.models import Resident


def test_halaman_login_tersedia(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"Masuk" in r.data


def test_root_mengarah_ke_login(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_berhasil_dan_masuk_dashboard(client, app):
    with app.app_context():
        assert Resident.query.filter_by(nomor_kamar="A1").first() is not None
    r = client.post("/login", data={"kamar": "A1", "password": "budi123"},
                    follow_redirects=False)
    assert r.status_code == 302
    r2 = client.get("/penghuni")
    assert r2.status_code == 200
    assert "Paket Saya" in r2.data.decode()


def test_login_salah_ditolak(client):
    r = client.post("/login", data={"kamar": "A1", "password": "salah"})
    assert r.status_code == 200
    assert "salah" in r.data.decode().lower()


def test_api_status_publik(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["data"]["state"] in {
        "IDLE_OPEN", "PAKET_MASUK", "MENUNGGU_TENANG", "ANALISIS",
        "TERSIMPAN", "TERSIMPAN_PERLU_MANUAL", "PENGAMBILAN", "DIAMBIL",
        "TUNGGU_ADMIN",
    }


def test_halaman_admin_butuh_login(client):
    r = client.get("/admin")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_admin_bisa_masuk_beranda(client):
    client.post("/login", data={"kamar": "admin", "password": "admin123"})
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Beranda" in r.data.decode()


def test_semua_halaman_admin_terbuka(client):
    client.post("/login", data={"kamar": "admin", "password": "admin123"})
    for path in ("/admin", "/admin/monitor", "/admin/deposits",
                 "/admin/events", "/admin/kamar", "/admin/simulasi"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_halaman_penghuni_terbuka(client):
    client.post("/login", data={"kamar": "A1", "password": "budi123"})
    for path in ("/penghuni", "/penghuni/daftar", "/ambil"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_stream_sse_tanpa_request_context(app):
    """Regresi: generator SSE berjalan setelah request context ditutup.

    Klien TIDAK dipakai sebagai context manager (`with`), karena itu akan
    mempertahankan request context dan menyembunyikan bug current_app.
    """
    c = app.test_client()
    r = c.get("/api/stream", buffered=False)
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"

    gen = r.response
    baris1 = next(gen)
    if isinstance(baris1, bytes):
        baris1 = baris1.decode()
    assert "terhubung" in baris1

    baris2 = next(gen)
    if isinstance(baris2, bytes):
        baris2 = baris2.decode()
    assert baris2.startswith("data: ")
    isi = json.loads(baris2[len("data: "):])
    assert isi["tipe"] == "status"
    assert "state" in isi["data"]

    gen.close()
