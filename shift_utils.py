"""
Vardiya + OEE tek kaynak — shift_utils.py (repo kökü)

DB-direkt tüketiciler (aynı Pi'de, .52) import eder:
  production_logger.py, production.py (API), downtime_monitor.py
Terminal (.51) DB'ye bağlanamaz → API'den okur (Adım 5).

Davranış:
  - shift_config + downtime_reasons.exclude_from_oee'yi okur, 60sn cache'ler
  - MQTT 'config' mesajında invalidate() → cache anında temizlenir
  - DB erişilemese bile detect_shift ASLA patlamaz (built-in fallback)
"""

import os
import time
import threading
import logging
from datetime import datetime, timedelta, date
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("shift_utils")

# DB hiç okunamazsa bile sistem çalışsın diye güvenli varsayılan (tek vardiya)
_FALLBACK_SHIFT = {
    "code": "vardiya_1", "label": "1. Vardiya",
    "start_hour": 8, "end_hour": 18, "latest_end": 19,
    "window_hours": 10, "planned_seconds": 36000, "display_order": 1, "is_active": True,
}

_DEFAULT_DB_URL = os.getenv(
    "DB_URL", "postgresql://yigitcanc:***REMOVED***@127.0.0.1:5432/cerkezkoy_db"
)
_TTL = 60.0  # saniye

_lock = threading.Lock()
_state = {"ts": 0.0, "shifts": [], "excluded": set()}
_db_url = _DEFAULT_DB_URL
_conn = None


def configure(db_url: str = None, ttl: float = None):
    """API/servis açılışında çağrılabilir (opsiyonel). DB URL / TTL override."""
    global _db_url, _TTL
    if db_url:
        _db_url = db_url
    if ttl:
        _TTL = ttl


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(_db_url)
        _conn.autocommit = True
    return _conn


def _refresh():
    """DB'den vardiya + hariç-kodları oku. Hata olursa eski cache'i koru."""
    global _conn
    try:
        db = _get_conn()
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT code, label, start_hour, end_hour, latest_end,
                   window_hours, planned_seconds, display_order, is_active, line_id
            FROM shift_config
            WHERE is_active = TRUE
            ORDER BY display_order, start_hour
        """)
        shifts = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT reason_code FROM downtime_reasons WHERE exclude_from_oee = TRUE
        """)
        excluded = {r["reason_code"] for r in cur.fetchall()}
        cur.close()

        if not shifts:                      # tablo boşsa fallback
            shifts = [dict(_FALLBACK_SHIFT)]

        _state["shifts"] = shifts
        _state["excluded"] = excluded
        _state["ts"] = time.time()
    except Exception as e:
        logger.error(f"shift_config okunamadı, eski cache kullanılıyor: {e}")
        _conn = None
        if not _state["shifts"]:            # hiç cache yoksa fallback yükle
            _state["shifts"] = [dict(_FALLBACK_SHIFT)]
            _state["ts"] = time.time()


def _ensure(force=False):
    with _lock:
        if force or (time.time() - _state["ts"] >= _TTL) or not _state["shifts"]:
            _refresh()


def invalidate():
    """MQTT 'config' reload geldiğinde çağrılır → sonraki okuma DB'den taze gelir."""
    with _lock:
        _state["ts"] = 0.0
    logger.info("shift_utils cache invalidate edildi (config reload)")


# ── Dışa açık API ──
def get_shifts(force=False, line_id=None):
    """line_id verilirse: o hatta özel satırlar; yoksa global (line_id IS NULL) satırlar."""
    _ensure(force)
    allrows = _state["shifts"]
    if line_id is not None:
        specific = [s for s in allrows if s.get("line_id") == line_id]
        if specific:
            return specific
    return [s for s in allrows if s.get("line_id") is None] or list(allrows)


def get_excluded_codes(force=False):
    """OEE'den hariç tutulacak reason_code'lar (mola + vardiya sonu)."""
    _ensure(force)
    return set(_state["excluded"])


def _in_window(h: int, start: int, end: int) -> bool:
    if end >= 24:
        end = 24
    if start < end:                  # aynı gün (08–18)
        return start <= h < end
    return h >= start or h < end      # gece yarısını geçen (22–06)


def detect_shift(dt: datetime = None, line_id: int = None):
    dt = dt or datetime.now()
    h = dt.hour
    shifts = get_shifts(line_id=line_id)
    for s in shifts:
        if _in_window(h, s["start_hour"], s["end_hour"]):
            return s["code"], s["label"]
    first = shifts[0]
    return first["code"], first["label"]


def detect_shift_code(dt: datetime = None, line_id: int = None) -> str:
    return detect_shift(dt, line_id)[0]


def shift_by_code(code: str, line_id: int = None):
    for s in get_shifts(line_id=line_id):
        if s["code"] == code:
            return s
    return get_shifts(line_id=line_id)[0]


def planned_seconds(code: str, line_id: int = None) -> int:
    return int(shift_by_code(code, line_id).get("planned_seconds") or _FALLBACK_SHIFT["planned_seconds"])


def resolve_shift_date(shift: dict, now: datetime):
    """Vardiya şu an bitmiş mi? Bittiyse hangi shift_date'e ait, değilse None.
    (production_logger'daki mantığın taşınmış hali — Adım 4'te oraya bağlanacak.)"""
    latest_end = shift["latest_end"]
    start_hour = shift["start_hour"]
    h = now.hour
    crosses_midnight = latest_end <= start_hour
    if not crosses_midnight:
        return now.date() if h >= latest_end else None
    else:
        if h < latest_end:
            return (now - timedelta(days=1)).date()
        elif h >= start_hour:
            return None
        else:
            return (now - timedelta(days=1)).date()