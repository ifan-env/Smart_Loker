"""Halaman admin: beranda, monitor, deposit, log, kelola penghuni, simulasi."""
from __future__ import annotations

import json
from datetime import datetime

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from werkzeug.security import generate_password_hash

from ..auth import admin_required
from ..models import Deposit, Event, Parcel, Resident, db

bp_admin = Blueprint("admin", __name__, url_prefix="/admin")


def _normalisasi_uid(nilai):
    """UID yang dikirim ESP32: hex tanpa separator. Terima tempelan 04:A1:B2."""
    bersih = "".join((nilai or "").split()).replace(":", "").replace("-", "")
    return bersih.upper() or None


def _locker():
    return current_app.extensions["locker"]


def _telegram_aktif():
    return bool(current_app.config["TELEGRAM_TOKEN"])


@bp_admin.get("")
@admin_required
def beranda():
    ringkasan = {
        "resident": Resident.query.filter_by(aktif=True, role="penghuni").count(),
        "tersimpan": Parcel.query.filter_by(status="tersimpan").count(),
        "perlu_manual": Deposit.query.filter_by(result="perlu_manual").count(),
        "anomali": Event.query.filter(Event.severity == "warning").count(),
    }
    aktivitas = Event.query.order_by(Event.id.desc()).limit(7).all()
    return render_template(
        "admin/beranda.html",
        ringkasan=ringkasan,
        aktivitas=aktivitas,
        status=_locker().status(),
        telegram_aktif=_telegram_aktif(),
        sekarang=datetime.now(),
    )


@bp_admin.get("/monitor")
@admin_required
def monitor():
    return render_template(
        "admin/monitor.html",
        status=_locker().status(),
        telegram_aktif=_telegram_aktif(),
    )


@bp_admin.get("/simulasi")
@admin_required
def simulasi():
    aktivitas = Event.query.order_by(Event.id.desc()).limit(8).all()
    return render_template(
        "admin/simulasi.html",
        aktivitas=aktivitas,
        status=_locker().status(),
        telegram_aktif=_telegram_aktif(),
    )


@bp_admin.get("/deposits")
@admin_required
def deposits():
    data = Deposit.query.order_by(Deposit.id.desc()).limit(50).all()
    warga = Resident.query.filter_by(aktif=True).order_by(Resident.nomor_kamar).all()
    return render_template("admin/deposits.html", deposits=data, warga=warga,
                           telegram_aktif=_telegram_aktif())


BATAS_LOG = 50


def _potong_log(severity: str, sebelum: int | None):
    """Ambil satu halaman log (id menurun) dan tandai apakah masih ada lagi."""
    q = Event.query.order_by(Event.id.desc())
    if severity:
        q = q.filter(Event.severity == severity)
    if sebelum:
        q = q.filter(Event.id < sebelum)
    baris = q.limit(BATAS_LOG + 1).all()
    lebih = len(baris) > BATAS_LOG
    return baris[:BATAS_LOG], lebih


@bp_admin.get("/events")
@admin_required
def events():
    aktif = (request.args.get("severity") or "").strip()
    data, lebih = _potong_log(aktif, None)
    return render_template(
        "admin/events.html", events=data, aktif=aktif, lebih=lebih,
        kursor=data[-1].id if data else None,
        telegram_aktif=_telegram_aktif())


@bp_admin.get("/events/more")
@admin_required
def events_more():
    """Potongan baris log berikutnya untuk pemuatan saat menggulir."""
    aktif = (request.args.get("severity") or "").strip()
    sebelum = request.args.get("before", type=int)
    if not sebelum:
        return jsonify({"ok": False, "data": None,
                        "error": "Kursor 'before' wajib berupa id event."}), 400
    data, lebih = _potong_log(aktif, sebelum)
    return jsonify({
        "ok": True,
        "data": {
            "html": render_template("admin/_events_rows.html", events=data),
            "kursor": data[-1].id if data else None,
            "lebih": lebih,
        },
        "error": None,
    })


def _cek_uid(uid, warga_id=None):
    """Kartu tidak boleh dipakai dua penghuni. -> pesan_error atau None."""
    pemilik = Resident.query.filter_by(rfid_uid=uid).first()
    if pemilik is not None and pemilik.id != warga_id:
        return (f"Kartu {uid} sudah dipakai {pemilik.nama} "
                f"({pemilik.nomor_kamar}).")
    return None


@bp_admin.route("/kamar", methods=["GET", "POST"])
@admin_required
def kamar():
    """Satu form serbaguna: `resident_id` kosong = tambah, terisi = edit."""
    if request.method == "POST":
        rid = request.form.get("resident_id") or ""
        nama = (request.form.get("nama") or "").strip()
        nomor = (request.form.get("nomor_kamar") or "").strip()
        sandi = request.form.get("password") or ""
        rfid = _normalisasi_uid(request.form.get("rfid_uid"))

        warga = db.session.get(Resident, int(rid)) if rid.isdigit() else None

        error = None
        if not nama or not nomor:
            error = "Nama dan nomor kamar wajib diisi."
        elif not rfid:
            error = "Kartu RFID wajib ditempel ke reader loker."
        elif not warga and len(sandi) < 6:
            error = "Password awal minimal 6 karakter wajib."
        else:
            bentrok_kamar = Resident.query.filter_by(nomor_kamar=nomor).first()
            if bentrok_kamar is not None and (warga is None or bentrok_kamar.id != warga.id):
                error = f"Kamar {nomor} sudah terdaftar."
            else:
                error = _cek_uid(rfid, warga.id if warga else None)

        if error:
            flash(error, "error")
            return redirect(url_for("admin.kamar"))

        if warga is None:                       # ---- tambah
            db.session.add(Resident(
                nama=nama, nomor_kamar=nomor,
                password_hash=generate_password_hash(sandi),
                rfid_uid=rfid, role="penghuni"))
            db.session.add(Event(
                type="KARTU_DIDAFTARKAN", severity="info",
                payload=json.dumps({"uid": rfid, "nama": nama}),
            ))
            flash(f"Penghuni {nama} ({nomor}) ditambahkan.", "ok")
        else:                                   # ---- edit
            lama = {"nama": warga.nama, "nomor_kamar": warga.nomor_kamar,
                    "rfid_uid": warga.rfid_uid}
            warga.nama = nama
            warga.nomor_kamar = nomor
            warga.rfid_uid = rfid
            db.session.add(Event(
                type="PENGHUNI_DIEDIT", severity="info",
                payload=json.dumps({"resident_id": warga.id, "lama": lama,
                                    "baru": {"nama": nama, "nomor_kamar": nomor,
                                             "rfid_uid": rfid}}),
            ))
            if lama["rfid_uid"] != rfid:
                db.session.add(Event(
                    type="KARTU_DIDAFTARKAN", severity="info",
                    payload=json.dumps({"resident_id": warga.id, "uid": rfid,
                                        "lama": lama["rfid_uid"]}),
                ))
            flash(f"Data {nama} ({nomor}) diperbarui.", "ok")
        db.session.commit()
        return redirect(url_for("admin.kamar"))

    edit_id = request.args.get("edit", type=int)
    return render_template(
        "admin/kamar.html",
        warga=Resident.query.order_by(Resident.nomor_kamar).all(),
        edit=db.session.get(Resident, edit_id) if edit_id else None,
        telegram_aktif=_telegram_aktif(),
    )


@bp_admin.post("/kamar/<int:resident_id>/kartu/hapus")
@admin_required
def hapus_kartu(resident_id):
    warga = db.session.get(Resident, resident_id)
    if warga is None:
        flash("Penghuni tidak ditemukan.", "error")
        return redirect(url_for("admin.kamar"))
    lama = warga.rfid_uid
    if not lama:
        flash(f"{warga.nama} belum punya kartu.", "info")
        return redirect(url_for("admin.kamar"))
    warga.rfid_uid = None
    db.session.add(Event(
        type="KARTU_DIHAPUS", severity="info",
        payload=json.dumps({"resident_id": warga.id, "uid": lama}),
    ))
    db.session.commit()
    flash(f"Kartu {lama} dilepas dari {warga.nama}.", "ok")
    return redirect(url_for("admin.kamar"))


@bp_admin.get("/uploads/<path:nama>")
@admin_required
def upload(nama):
    from flask import send_from_directory
    return send_from_directory(current_app.config["UPLOAD_DIR"], nama)


@bp_admin.post("/assign/<int:deposit_id>")
@admin_required
def assign_form(deposit_id):
    resident_id = request.form.get("resident_id")
    hasil = _locker().aksi_admin(
        "assign", deposit_id=deposit_id, resident_id=int(resident_id))
    flash("Penerima ditetapkan." if hasil.get("ok") else hasil.get("error", "Gagal"),
          "ok" if hasil.get("ok") else "error")
    return redirect(url_for("admin.deposits"))
