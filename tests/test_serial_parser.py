"""Uji parser protokol serial — tanpa membuka port."""
from app.services.serial_service import urai_baris


def jenis(baris):
    return urai_baris(baris)[0]


def muatan(baris):
    return urai_baris(baris)[1]


def test_event_dasar():
    assert urai_baris("EVT DOOR CLOSED") == ("DOOR", {"state": "closed"})
    assert urai_baris("EVT DOOR OPEN") == ("DOOR", {"state": "open"})
    assert urai_baris("EVT LOCK LOCKED") == ("LOCK", {"state": "locked"})
    assert urai_baris("EVT LOCK RELEASED") == ("LOCK", {"state": "released"})
    assert urai_baris("EVT OBJECT PRESENT") == ("OBJECT", {"state": "present"})
    assert urai_baris("EVT OBJECT EMPTY") == ("OBJECT", {"state": "empty"})


def test_event_lampu_dan_solenoid():
    assert urai_baris("EVT LIGHT ON") == ("LIGHT", {"state": "on"})
    assert urai_baris("EVT LIGHT OFF") == ("LIGHT", {"state": "off"})
    assert urai_baris("EVT SOLENOID ON 2000") == ("SOLENOID", {"state": "on", "ms": 2000})
    assert urai_baris("EVT SOLENOID OFF") == ("SOLENOID", {"state": "off"})


def test_dist_rfid_alert():
    assert muatan("EVT DIST 12.5") == {"cm": 12.5}
    assert urai_baris("EVT RFID a1b2c3d4") == ("RFID", {"uid": "A1B2C3D4"})
    assert urai_baris("EVT ALERT DOOR_FORCED") == ("ALERT", {"kind": "DOOR_FORCED"})
    assert urai_baris("EVT ALERT LOCK_FORCED") == ("ALERT", {"kind": "LOCK_FORCED"})
    assert urai_baris("EVT BUTTON PRESS") == ("BUTTON", {"state": "press"})


def test_status_diubah_jadi_dict():
    baris = ("STATUS door=CLOSED lock=LOCKED solenoid=OFF dist=12.3 "
             "baseline=25.0 object=PRESENT light=ON")
    j, p = urai_baris(baris)
    assert j == "STATUS"
    assert p["door"] == "CLOSED" and p["light"] == "ON" and p["baseline"] == "25.0"


def test_jawaban_perintah():
    assert jenis("PONG") == "PONG"
    assert jenis("OK CAL baseline=25.0") == "OK"
    assert jenis("ERR perintah tidak dikenal: FOO") == "ERR"
    assert jenis("Locker siap.") is None
    assert jenis("") is None
    assert jenis("EVT NGAWUR SEKALI") == "EVT_TAK_DIKENAL"


def test_baris_rusak_tidak_melempar_eksepsi():
    assert urai_baris("EVT DIST") == ("DIST", {"cm": None})
    assert urai_baris("EVT RFID") == (None, None)
    assert urai_baris("EVT SOLENOID ON") == ("SOLENOID", {"state": "on", "ms": None})
