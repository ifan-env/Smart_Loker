"""Autentikasi sesi sederhana + dekorator pembatas akses."""
from __future__ import annotations

from functools import wraps

from flask import redirect, session, url_for

from .models import Resident, db


def masuk(resident: Resident):
    session.clear()
    session["uid"] = resident.id
    session["role"] = resident.role


def keluar():
    session.clear()


def pengguna():
    uid = session.get("uid")
    return db.session.get(Resident, uid) if uid else None


def login_required(fn):
    @wraps(fn)
    def bungkus(*a, **kw):
        if not pengguna():
            return redirect(url_for("web.login"))
        return fn(*a, **kw)
    return bungkus


def admin_required(fn):
    @wraps(fn)
    def bungkus(*a, **kw):
        u = pengguna()
        if not u:
            return redirect(url_for("web.login"))
        if not u.admin:
            return redirect(url_for("web.beranda"))
        return fn(*a, **kw)
    return bungkus
