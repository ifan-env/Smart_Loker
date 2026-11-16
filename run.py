"""Jalankan aplikasi.

Penting: use_reloader=False. Reloader Flask menjalankan proses kedua yang
akan berebut port serial dengan proses pertama.

Server terbuka di 0.0.0.0 supaya bisa diakses dari HP/device lain di
jaringan yang sama. Koneksi masuk tetap bisa diblokir Windows Firewall
(jaringan berkategori Public) — lihat README bagian "Akses dari HP".
"""
import logging
import socket

from app import create_app

app = create_app()


def alamat_lan():
    """Alamat IPv4 selain localhost, supaya user tahu URL HP-nya."""
    try:
        # UDP socket: tidak perlu koneksi sungguhan, hanya untuk membaca
        # alamat yang dipakai sistem untuk menuju jaringan.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print("\n  Smart Parcel Locker")
    print("  Lokal : http://127.0.0.1:5000")
    lan = alamat_lan()
    if lan:
        print(f"  Jaringan: http://{lan}:5000")
    print("  Admin: admin / admin123   |   Penghuni: A1 / budi123\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False,
            threaded=True)
