"""Uji menu akun di pojok kiri bawah sidebar admin."""
import pytest


@pytest.fixture
def admin(client):
    client.post("/login", data={"kamar": "admin", "password": "admin123"})
    return client


def test_menu_profil_ada_di_semua_halaman_admin(admin):
    for path in ("/admin", "/admin/monitor", "/admin/kamar",
                 "/admin/deposits", "/admin/events", "/admin/simulasi"):
        t = admin.get(path).data.decode()
        assert 'id="menu-profil"' in t, f"{path} tanpa menu profil"
        assert 'id="buka-profil"' in t, f"{path} tanpa tombol menu"


def test_menu_profil_menampilkan_akun_dan_keluar(admin):
    t = admin.get("/admin").data.decode()
    assert "Pengelola" in t
    assert 'href="/logout"' in t
    assert "Keluar" in t


def test_tombol_titik_dan_tanpa_menu_navigasi(admin):
    """Tombol pembuka berupa ikon titik tiga, tanpa tautan navigasi."""
    t = admin.get("/admin").data.decode()
    assert 'title="Menu akun">⋯</button>' in t
    for tautan in ('href="/admin/kamar"', 'href="/admin/events"'):
        assert tautan not in t.split('id="menu-profil"')[1].split("</div>")[0], \
            "tautan navigasi harusnya sudah dihapus dari menu"
    assert "Kelola penghuni" not in t.split('class="menu-profil"')[1]
    assert "Log kejadian" not in t.split('class="menu-profil"')[1]


def test_tombol_titik_punya_label_nama(admin):
    """Tombol pembuka kini ikon titik tiga saja, nama ada di dalam menu."""
    t = admin.get("/admin").data.decode()
    assert 'title="Menu akun">⋯</button>' in t
    assert "Pengelola" in t


def test_logout_mengakhiri_sesi(admin):
    assert admin.get("/admin").status_code == 200
    r = admin.get("/logout", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    assert admin.get("/admin").status_code == 302


def test_menu_tidak_terpotong_overflow_sidebar():
    """Regresi: `.sidebar` dan `.app` memakai overflow hidden.

    Menu harus `position: fixed` (lolos kliping) — kalau kembali ke
    `absolute`, menu akan terpotong dan tampak "tidak berfungsi".
    """
    import pathlib
    import re
    from app import create_app  # noqa: F401  (pastikan paket bisa diimpor)

    css = pathlib.Path("app/static/style.css").read_text(encoding="utf-8")
    m = re.search(r"\.menu-profil\s*\{([^}]*)\}", css)
    assert m, "aturan .menu-profil hilang"
    isi = m.group(1)
    assert "position: fixed" in isi
    assert "position: absolute" not in isi

    # Sidebar memang mengklip isinya; itu alasan menu harus fixed.
    sidebar = re.search(r"\.sidebar\s*\{([^}]*)\}", css).group(1)
    assert "overflow-x: hidden" in sidebar


def test_js_menu_menghitung_posisi(admin):
    t = admin.get("/admin").data.decode()
    assert "getBoundingClientRect" in t, "posisi menu harus dihitung dari tombol"
    assert "addEventListener('resize'" in t
    assert "addEventListener('scroll'" in t


def test_script_menu_ada_di_semua_halaman(admin):
    """Regresi: kamar/monitor/simulasi/events mengisi `block skrip` sendiri.

    Kalau script menu ditaruh di block itu, halaman-halaman tersebut akan
    kehilangan script-nya dan tombol menu diam saja saat diklik.
    """
    for path in ("/admin", "/admin/monitor", "/admin/deposits",
                 "/admin/events", "/admin/kamar", "/admin/simulasi"):
        t = admin.get(path).data.decode()
        assert 'id="menu-profil"' in t, f"{path}: elemen menu hilang"
        assert 'id="buka-profil"' in t, f"{path}: tombol menu hilang"
        assert "getBoundingClientRect" in t, f"{path}: script menu hilang"


def test_template_admin_render_tanpa_error(admin):
    """Template harus lolos render, termasuk komentar yang mirip tag Jinja."""
    for path in ("/admin", "/admin/monitor", "/admin/deposits",
                 "/admin/events", "/admin/kamar", "/admin/simulasi"):
        assert admin.get(path).status_code == 200, f"{path} gagal render"


def test_menu_profil_isi_tautan_valid(admin):
    """Tautan di menu harus mengarah ke halaman yang benar-benar ada."""
    for tujuan in ("/admin/kamar", "/admin/events"):
        r = admin.get(tujuan)
        assert r.status_code == 200, f"{tujuan} -> {r.status_code}"
    r = admin.get("/logout", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
