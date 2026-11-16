"""OTP 6 digit: sekali pakai, ada masa berlaku, dibatasi percobaan."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone


def buat(kode: str, salt: str | None = None) -> str:
    """Simpan sebagai `salt$hash` — OTP asli tidak pernah disimpan."""
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256(f"{salt}:{kode}".encode()).hexdigest()
    return f"{salt}${h}"


def periksa(disimpan: str, kode: str) -> bool:
    try:
        salt, _ = disimpan.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(buat(kode, salt), disimpan)


def baru(panjang: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(panjang))


def kedaluwarsa(menit: int = 30) -> datetime:
    """Naive UTC, sama seperti models.sekarang()."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=menit)
