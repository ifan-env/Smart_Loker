"""Endpoint JSON + Server-Sent Events untuk dashboard admin."""
from __future__ import annotations

import json
import logging
import queue

from flask import Blueprint, Response, current_app, jsonify, request

from ..auth import admin_required, login_required, pengguna
from ..models import Deposit, Event, Parcel, PickupCode, Resident, db
from ..services import matcher

bp_api = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)


def _locker():
    return current_app.extensions["locker"]


def _ok(data=None, **kw):
    hasil = {"ok": True, "data": data}
    hasil.update(kw)
    return jsonify(hasil)


def _gagal(pesan, kode=400):
    return jsonify({"ok": False, "error": pesan}), kode


# ---------------------------------------------------------------- publik
@bp_api.get("/status")
def status():
    return _ok(_locker().status())


@bp_api.get("/stream")
def stream():
    """SSE: status sensor + event. Dipakai halaman monitor admin.

    Semua referensi diambil SEBELUM generator dibuat. Body generator berjalan
    setelah request context ditutup (Werkzeug mengiterasi respons secara lazy),
    jadi `current_app` sudah tidak tersedia di dalamnya.
    """
    app = current_app._get_current_object()
    bus = app.extensions["bus"]
    locker = app.extensions["locker"]
    # Status awal dihitung SEKARANG, selagi request context masih hidup.
    # Body generator berjalan setelah context ditutup, jadi tidak boleh
    # memakai current_app (maupun app_context baru) di dalamnya.
    awal = json.dumps({"tipe": "status", "data": locker.status()}, default=str)

    def aliran():
        q = bus.daftar()
        try:
            yield ": terhubung\n\n"
            yield f"data: {awal}\n\n"
            while True:
                try:
                    pesan = q.get(timeout=15)
                    yield f"data: {pesan}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            raise
        except Exception:
            log.exception("Aliran SSE gagal")
            raise
        finally:
            bus.lepas(q)

    return Response(aliran(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- penghuni
@bp_api.get("/penghuni/paket")
@login_required
def paket_saya():
    u = pengguna()
    data = [{
        "id": p.id, "resi": p.tracking_number, "status": p.status,
        "stored_at": p.stored_at, "picked_at": p.picked_at,
    } for p in Parcel.query.filter_by(resident_id=u.id)
        .order_by(Parcel.created_at.desc()).all()]
    return _ok(data)


@bp_api.post("/penghuni/paket")
@login_required
def daftar_paket():
    u = pengguna()
    isi = request.get_json(silent=True) or request.form
    resi = (isi.get("resi") or "").strip()
    if not resi or len(resi) < 6:
        return _gagal("Nomor resi wajib diisi, minimal 6 karakter.")
    p = Parcel(
        resident_id=u.id,
        tracking_number=resi.upper(),
        tracking_norm=matcher.normalisasi(resi),
        status="menunggu",
    )
    db.session.add(p)
    db.session.commit()
    return _ok({"id": p.id}, pesan="Resi didaftarkan. Paket akan dikenali saat tiba.")


# ---------------------------------------------------------------- admin
@bp_api.get("/admin/deposits")
@admin_required
def daftar_deposit():
    batas = min(int(request.args.get("limit", 30)), 200)
    status = request.args.get("status")
    q = Deposit.query.order_by(Deposit.id.desc())
    if status:
        q = q.filter(Deposit.result == status)
    data = [{
        "id": d.id,
        "waktu": d.captured_at,
        "foto": f"/uploads/{d.photo_path}" if d.photo_path else None,
        "svm_kelas": d.svm_kelas,
        "svm_margin": d.svm_margin,
        "ocr_resi": d.ocr_resi,
        "ocr_nama": d.ocr_nama,
        "match": d.match_method,
        "result": d.result,
        "parcel_id": d.parcel_id,
        "penerima": d.parcel.resident.nama if d.parcel and d.parcel.resident else None,
    } for d in q.limit(batas).all()]
    return _ok(data)


@bp_api.post("/admin/deposits/<int:deposit_id>/assign")
@admin_required
def assign(deposit_id):
    isi = request.get_json(silent=True) or request.form
    resident_id = isi.get("resident_id")
    if not str(resident_id or "").isdigit():
        return _gagal("resident_id wajib angka.")
    hasil = _locker().aksi_admin("assign", deposit_id=deposit_id,
                                 resident_id=int(resident_id))
    return (_ok(hasil) if hasil.get("ok") else _gagal(hasil.get("error", "gagal")))


@bp_api.post("/admin/<aksi>")
@admin_required
def kontrol(aksi):
    if aksi not in {"unlock", "unlock_paksa", "lock", "cal", "light", "reset", "simulasi", "kamera"}:
        return _gagal("aksi tidak dikenal", 404)
    isi = request.get_json(silent=True) or request.form or {}
    hasil = _locker().aksi_admin(aksi, **isi)
    return (_ok(hasil) if hasil.get("ok") else _gagal(hasil.get("error", "gagal")))


@bp_api.get("/admin/events")
@admin_required
def daftar_event():
    batas = min(int(request.args.get("limit", 50)), 500)
    q = Event.query.order_by(Event.id.desc())
    if request.args.get("severity"):
        q = q.filter(Event.severity == request.args["severity"])
    data = [{
        "id": e.id, "type": e.type, "severity": e.severity,
        "payload": e.payload, "waktu": e.created_at,
    } for e in q.limit(batas).all()]
    return _ok(data)


@bp_api.get("/admin/ringkasan")
@admin_required
def ringkasan():
    return _ok({
        "resident": Resident.query.count(),
        "tersimpan": Parcel.query.filter_by(status="tersimpan").count(),
        "perlu_manual": Deposit.query.filter_by(result="perlu_manual").count(),
        "anomali": Event.query.filter(Event.severity == "warning").count(),
    })


@bp_api.get("/admin/kamar")
@admin_required
def daftar_kamar():
    return _ok([{
        "id": r.id, "nama": r.nama, "kamar": r.nomor_kamar,
        "rfid": r.rfid_uid, "role": r.role,
    } for r in Resident.query.order_by(Resident.nomor_kamar).all()])


# ------------------------------------------------- daftar kartu RFID (tap)
@bp_api.get("/admin/kartu/status")
@admin_required
def kartu_status():
    """Pantau sesi tap. `token` opsional: sesi lama tidak dianggap milik form ini."""
    token = (request.args.get("token") or "").strip()
    data = _locker().info_daftar_kartu_hidup()
    if data and token and data["token"] != token:
        return _ok(None)
    return _ok(data)


@bp_api.post("/admin/kartu/mulai")
@admin_required
def kartu_mulai():
    """Buka sesi tap untuk form. Tanpa `resident_id` saat menambah penghuni."""
    isi = request.get_json(silent=True) or request.form or {}
    resident_id = isi.get("resident_id")
    if resident_id in ("", None):
        resident_id = None
    elif not str(resident_id).isdigit():
        return _gagal("resident_id wajib angka.")
    else:
        resident_id = int(resident_id)
    hasil = _locker().mulai_daftar_kartu(resident_id,
                                         int(isi.get("detik") or 120))
    return _ok(hasil.get("data")) if hasil.get("ok") else _gagal(hasil.get("error"))


@bp_api.post("/admin/kartu/batal")
@admin_required
def kartu_batal():
    return _ok(_locker().batal_daftar_kartu())
