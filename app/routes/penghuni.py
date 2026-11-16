"""Halaman penghuni: login, daftar paket, detail, ambil dengan OTP."""
from __future__ import annotations

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from werkzeug.security import check_password_hash

from ..auth import keluar, login_required, masuk, pengguna
from ..models import Deposit, Parcel, Resident, db

bp_web = Blueprint("web", __name__)


@bp_web.get("/")
def beranda():
    if not pengguna():
        return redirect(url_for("web.login"))
    return redirect(url_for("admin.beranda") if pengguna().admin
                    else url_for("web.dashboard"))


@bp_web.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        kamar = (request.form.get("kamar") or "").strip()
        sandi = request.form.get("password") or ""
        warga = Resident.query.filter_by(nomor_kamar=kamar, aktif=True).first()
        if warga and check_password_hash(warga.password_hash, sandi):
            masuk(warga)
            return redirect(url_for("web.beranda"))
        flash("Nomor kamar atau password salah.", "error")
    return render_template("login.html")


@bp_web.get("/logout")
def logout():
    keluar()
    return redirect(url_for("web.login"))


@bp_web.get("/penghuni")
@login_required
def dashboard():
    u = pengguna()
    paket = (Parcel.query.filter_by(resident_id=u.id)
             .order_by(Parcel.created_at.desc()).all())
    return render_template("penghuni/index.html", paket=paket)


@bp_web.get("/penghuni/daftar")
@login_required
def daftar():
    return render_template("penghuni/daftar.html")


@bp_web.post("/penghuni/paket")
@login_required
def tambah_paket():
    from ..services import matcher

    u = pengguna()
    resi = (request.form.get("resi") or "").strip()
    if len(resi) < 6:
        flash("Nomor resi minimal 6 karakter.", "error")
        return redirect(url_for("web.daftar"))
    db.session.add(Parcel(resident_id=u.id, tracking_number=resi.upper(),
                          tracking_norm=matcher.normalisasi(resi),
                          status="menunggu"))
    db.session.commit()
    flash("Resi didaftarkan. Paket akan dikenali otomatis saat tiba.", "ok")
    return redirect(url_for("web.dashboard"))


@bp_web.get("/penghuni/paket/<int:pid>")
@login_required
def detail(pid):
    u = pengguna()
    parcel = db.session.get(Parcel, pid)
    if parcel is None or (parcel.resident_id != u.id and not u.admin):
        flash("Paket tidak ditemukan.", "error")
        return redirect(url_for("web.dashboard"))
    deposit = (Deposit.query.filter_by(parcel_id=parcel.id)
               .order_by(Deposit.id.desc()).first())
    return render_template("penghuni/detail.html", parcel=parcel, deposit=deposit)


@bp_web.route("/ambil", methods=["GET", "POST"])
@login_required
def ambil():
    hasil = None
    pesan = None
    if request.method == "POST":
        kode = (request.form.get("kode") or "").strip()
        if not kode:
            kode = "".join((request.form.get(f"d{i}") or "") for i in range(1, 7))
        ok, pesan = current_app.extensions["locker"].verifikasi_otp(kode)
        hasil = "ok" if ok else "err"
    return render_template("penghuni/ambil.html", hasil=hasil, pesan=pesan)


@bp_web.get("/uploads/<path:nama>")
@login_required
def upload(nama):
    """Foto bukti hanya untuk pengguna yang login."""
    from flask import send_from_directory
    return send_from_directory(current_app.config["UPLOAD_DIR"], nama)
