"""Notifikasi Telegram. Tanpa token, semua pesan hanya ditulis ke log.

Dengan begini alur loker tetap bisa diuji penuh sebelum bot dibuat.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    def __init__(self, token="", chat_admin=""):
        self.token = (token or "").strip()
        self.chat_admin = (chat_admin or "").strip()

    @property
    def aktif(self):
        return bool(self.token)

    def kirim(self, chat_id: str | None, teks: str) -> bool:
        if not chat_id:
            log.info("[notif dilewati, tanpa chat_id] %s", teks.replace("\n", " | "))
            return False
        if not self.aktif:
            log.info("[notif mock -> %s] %s", chat_id, teks.replace("\n", " | "))
            return False
        try:
            r = requests.post(
                _API.format(token=self.token),
                json={"chat_id": chat_id, "text": teks, "parse_mode": "HTML"},
                timeout=8,
            )
            if r.status_code != 200:
                log.warning("Telegram menolak (%s): %s", r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("Telegram gagal: %s", exc)
            return False

    # ---------- pesan siap pakai ----------
    def paket_masuk(self, resident, parcel, kode_otp=None):
        teks = (
            f"<b>Paket tiba</b>\n"
            f"Halo {resident.nama} (kamar {resident.nomor_kamar}),\n"
            f"ada paket untuk Anda di loker.\n"
        )
        if parcel and parcel.tracking_number:
            teks += f"Resi: <code>{parcel.tracking_number}</code>\n"
        if kode_otp:
            teks += f"Kode ambil: <code>{kode_otp}</code>\n"
            teks += "Buka halaman /ambil untuk memasukkan kode.\n"
        teks += "Anda juga bisa menempelkan kartu RFID di loker."
        return self.kirim(resident.telegram_chat_id, teks)

    def perlu_manual(self, deposit_id, kelas, alasan):
        return self.kirim(
            self.chat_admin,
            f"<b>Perlu tindakan admin</b>\nDeposit #{deposit_id}\n"
            f"Kelas SVM: {kelas}\nAlasan: {alasan}",
        )

    def anomali(self, judul, detail=""):
        return self.kirim(self.chat_admin, f"<b>Anomali</b>\n{judul}\n{detail}")
