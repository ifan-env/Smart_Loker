# Smart Parcel Locker — Web & Backend (Flask)

Loker paket pintar: kurir memasukkan paket dan menutup pintu, sistem yang
mendeteksi, memotret, mengklasifikasi (SVM), mengenali penerima (barcode/OCR),
lalu mengirim notifikasi. Penghuni mengambil dengan RFID atau OTP.

## Menjalankan

```powershell
cd "D:\Sekolah\tugas\kuliah\Sems 5\Project\Smart_Loker"
pip install -r requirements.txt      # sekali saja
copy .env.example .env               # lalu sesuaikan
python run.py
```

Buka <http://127.0.0.1:5000> — akun awal: `admin/admin123`, `A1/budi123`,
`A2/siti123`. **Ganti sebelum dipakai sungguhan.**

Secara bawaan `SERIAL_MODE=mock` dan `CAMERA_MODE=mock`, jadi aplikasi jalan
penuh tanpa hardware. Halaman admin punya tombol skenario simulasi.

## Akses dari HP / device lain di Wi-Fi

Server sudah terbuka di `0.0.0.0:5000` (lihat `run.py`), jadi HP bisa membuka
halaman admin. `run.py` mencetak alamat jaringannya saat start, contoh:

```
  Lokal   : http://127.0.0.1:5000
  Jaringan: http://192.168.1.6:5000
```

Buka `http://<IP itu>:5000` dari HP yang satu Wi-Fi.

Kalau tidak bisa terhubung, Windows Firewall memblokir koneksi masuk —
terutama kalau jaringan Wi-Fi berkategori **Public**. Buka PowerShell
**sebagai Administrator** lalu jalankan:

```powershell
New-NetFirewallRule -DisplayName "Smart Locker Flask (5000)" `
  -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

Atau set saja jaringan Wi-Fi ke Private: **Settings → Network & internet →
Wi-Fi → (nama jaringan) → profil jaringan → Private**.

Pastikan `app.run(host="0.0.0.0")` — jangan diganti `127.0.0.1`, karena itu
hanya bisa diakses dari laptop itu sendiri.

## Pindah ke hardware nyata

1. `python scripts/cek_kamera.py --simpan` → lihat snapshot dan nama perangkat,
   lalu catat yang mengarah ke dalam loker.
2. Isi `.env`: `CAMERA_MODE=real`, `CAMERA_NAMA=<nama perangkat>` (lebih stabil
   daripada `CAMERA_INDEX` yang bergeser saat DroidCam/OBS aktif),
   `SERIAL_MODE=real`, `SERIAL_PORT=COM21`.
3. Tombol **"Titip Paket"**: satu kaki ke **GPIO 4**, satu kaki ke **GND**
   (internal pull-up, tidak perlu resistor).
4. Tutup Serial Monitor Arduino IDE (port tidak bisa dipakai dua aplikasi).
5. Jalankan `python run.py`. Di halaman admin tekan **Kalibrasi** saat loker
   kosong dan pintu **tertutup**.

## Struktur

```
app/
  config.py        semua parameter (atau .env)
  models.py        residents, parcels, deposits, pickup_codes, events, locker_state
  routes/          penghuni.py (web), admin.py (halaman), api.py (JSON + SSE)
  services/
    features.py    ekstraksi fitur — SALINAN PERSIS dari vision/svm_epoch.py
    svm_service.py memuat model sekali, klasifikasi()
    resi_service.py barcode (OpenCV) + OCR (vision/ocr_resi.py)
    matcher.py     normalisasi resi, fuzzy nama
    serial_service.py thread serial + parser + simulator
    locker_service.py state machine (satu-satunya penulis events/locker_state)
    camera_service.py frame webcam / mock
    notify.py      Telegram (tanpa token = log saja)
    otp.py         OTP 6 digit, hash, sekali pakai
  templates/
    admin/_shell.html      sidebar + kepala halaman
    web/_shell.html        top bar untuk penghuni
  static/style.css         sistem desain (token warna, kartu, pill, tabel)
scripts/           cek_kamera.py, uji_model_folder.py
tests/             pytest
```

## Halaman

| URL | Untuk | Isi |
|---|---|---|
| `/login` | semua | masuk dengan nomor kamar + password |
| `/admin` | admin | beranda: ringkasan, aktivitas, kondisi perangkat |
| `/admin/monitor` | admin | monitor realtime (SSE): ilustrasi loker berengsel samping, state machine, lampu, hasil analisis SVM/OCR, foto bukti, aktivitas |
| `/admin/deposits` | admin | riwayat deposit + penetapan penerima manual |
| `/admin/events` | admin | log kejadian dengan filter tingkat |
| `/admin/kamar` | admin | tabel penghuni, form tambah/edit penghuni (RFID lewat tempel kartu) |
| `/admin/simulasi` | admin | 6 skenario uji (hanya saat `SERIAL_MODE=mock`) |
| `/penghuni` | penghuni | daftar paket |
| `/penghuni/daftar` | penghuni | daftarkan resi |
| `/penghuni/paket/<id>` | penghuni | detail + foto bukti |
| `/ambil` | penghuni | ambil paket dengan OTP / RFID |

Desain antarmuka dibuat di FigCraft (dokumen "Smart Locker") dan diterapkan ke
template: sidebar lavender, kartu putih, aksen ungu, tinta `#1C1C22`.


## Uji

```powershell
pytest -q
python scripts/uji_model_folder.py     # akurasi + confusion matrix untuk laporan
```

## Protokol serial

Perintah: `UNLOCK [ms]`, `LOCK`, `LIGHT ON|OFF`, `STATUS`, `CAL`, `PING`.
Event: `EVT DOOR|LOCK|OBJECT|LIGHT|SOLENOID ...`, `EVT DIST <cm>`,
`EVT RFID <UID>`, `EVT BUTTON PRESS`, `EVT ALERT DOOR_FORCED|LOCK_FORCED`.

## Daftar kartu RFID

Tidak ada halaman terpisah. Kartu didaftarkan **di dalam form penghuni**
(`/admin/kamar`), baik saat menambah maupun mengedit:

1. Isi nama dan nomor kamar, lalu tekan **Tempel kartu**.
2. Tempelkan kartu ke reader loker — field RFID terisi otomatis (read-only,
   tidak bisa diketik). Form menampilkan sisa waktu 120 detik.
3. Tekan **Tambah penghuni** / **Simpan perubahan**.

Tombol **Edit** di tabel memuat data penghuni ke form yang sama (nama, nomor
kamar, RFID; password tidak diubah dari sini). **Lepas** menghapus `rfid_uid`
tanpa menghapus akun.

Selama sesi tempel kartu aktif, tap RFID hanya ditahan di server dan tidak
diproses sebagai pengambilan paket — kartu yang belum terdaftar pun aman
ditempel. Satu kartu hanya boleh dipakai satu penghuni; kalau sudah terpakai,
form langsung memberi tahu pemiliknya dan penyimpanan ditolak.

Firmware yang dipakai: `Arduino/locker_firmware2/locker_firmware2.ino`
(mendukung lampu dan tombol "Titip Paket" di GPIO 4).
