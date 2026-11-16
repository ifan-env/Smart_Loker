"""Model database (SQLite via SQLAlchemy).

Aturan penulisan: `locker_state` dan `events` hanya ditulis oleh thread
`locker_service`. Route membaca lewat service, tidak menulis langsung.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def sekarang():
    """Waktu sekarang dalam UTC (naive), konsisten dengan cara SQLite menyimpan."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Resident(db.Model):
    __tablename__ = "residents"

    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(80), nullable=False)
    nomor_kamar = db.Column(db.String(20), nullable=False, unique=True)
    telegram_chat_id = db.Column(db.String(40))
    rfid_uid = db.Column(db.String(32), unique=True)  # hex uppercase, tanpa separator
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(12), nullable=False, default="penghuni")
    aktif = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=sekarang)

    @property
    def admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<Resident {self.nomor_kamar} {self.nama}>"


class Parcel(db.Model):
    __tablename__ = "parcels"

    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey("residents.id"))
    tracking_number = db.Column(db.String(40), index=True)
    tracking_norm = db.Column(db.String(40), index=True)
    status = db.Column(db.String(20), nullable=False, default="menunggu")
    created_at = db.Column(db.DateTime, nullable=False, default=sekarang)
    stored_at = db.Column(db.DateTime)
    picked_at = db.Column(db.DateTime)

    resident = db.relationship("Resident", backref="parcels")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('menunggu','tersimpan','perlu_manual','diambil','anomali')",
            name="ck_parcel_status",
        ),
    )


class Deposit(db.Model):
    """Satu siklus penitipan (satu kali kurir memasukkan & menutup pintu)."""

    __tablename__ = "deposits"

    id = db.Column(db.Integer, primary_key=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey("parcels.id"))
    captured_at = db.Column(db.DateTime, nullable=False, default=sekarang)
    photo_path = db.Column(db.String(255))
    svm_kelas = db.Column(db.String(20))
    svm_margin = db.Column(db.Float)
    svm_skor = db.Column(db.Text)          # JSON
    ocr_resi = db.Column(db.String(40))
    ocr_nama = db.Column(db.String(80))
    raw_ocr = db.Column(db.Text)           # JSON
    match_method = db.Column(db.String(12))  # barcode | resi | nama | manual | None
    result = db.Column(db.String(20))        # diterima | ditolak | perlu_manual
    attempt_no = db.Column(db.Integer, nullable=False, default=1)

    parcel = db.relationship("Parcel", backref="deposits")


class PickupCode(db.Model):
    __tablename__ = "pickup_codes"

    id = db.Column(db.Integer, primary_key=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey("parcels.id"), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=sekarang)

    parcel = db.relationship("Parcel", backref="pickup_codes")


class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(40), nullable=False, index=True)
    severity = db.Column(db.String(10), nullable=False, default="info")
    payload = db.Column(db.Text)     # JSON
    photo_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=sekarang, index=True)


class LockerState(db.Model):
    """Satu baris (id=1) menyimpan kondisi loker agar pulih setelah restart."""

    __tablename__ = "locker_state"

    id = db.Column(db.Integer, primary_key=True, default=1)
    state = db.Column(db.String(24), nullable=False, default="IDLE_OPEN")
    since = db.Column(db.DateTime, nullable=False, default=sekarang)
    active_deposit_id = db.Column(db.Integer)
    reject_count = db.Column(db.Integer, nullable=False, default=0)
    last_object_signature = db.Column(db.String(40))
    auto_unlock_count = db.Column(db.Integer, nullable=False, default=0)
    auto_unlock_reset_at = db.Column(db.DateTime)
    baseline_cm = db.Column(db.Float)
    analysis_token = db.Column(db.String(40))
    catatan = db.Column(db.String(255))


def ambil_state():
    """Ambil (atau buat) baris state tunggal."""
    baris = db.session.get(LockerState, 1)
    if baris is None:
        baris = LockerState(id=1)
        db.session.add(baris)
        db.session.commit()
    return baris
