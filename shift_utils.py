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

_DEFAULT_DB_URL = os.getenv("DB_URL")
_TTL = 60.0  # saniye

_lock = threading.Lock()
_state = {"ts": 0.0, "shifts": [], "excluded": set(), "overrides": []}
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

    if not _db_url:
        raise RuntimeError(
            "DB_URL ortam değişkeni tanımlı değil; "
            "gizli DB bilgileri kod içinden okunmaz"
        )

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
                   window_hours, planned_seconds, display_order, is_active,
                   line_id, day_of_week
            FROM shift_config
            WHERE is_active = TRUE
            ORDER BY display_order, start_hour
        """)
        shifts = [dict(r) for r in cur.fetchall()]

        # Tarihe özel istisnalar (son 60 gün + gelecek) — cache'e alınır
        cur.execute("""
            SELECT override_date, code, line_id, label, start_hour, end_hour,
                   latest_end, window_hours, planned_seconds
            FROM shift_overrides
            WHERE override_date >= CURRENT_DATE - 60
        """)
        _state["overrides"] = [dict(r) for r in cur.fetchall()]

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
def get_shifts(force=False, line_id=None, dt=None):
    """Çözümleme sırası: tarih istisnası → gün kuralı → global varsayılan.
    dt: date veya datetime; None ise bugün."""
    _ensure(force)
    d = dt or datetime.now()
    the_date = d.date() if isinstance(d, datetime) else d
    pg_dow = (the_date.weekday() + 1) % 7      # 0=Pazar ... 6=Cumartesi

    def _line_filter(rows):
        if line_id is not None:
            spec = [r for r in rows if r.get("line_id") == line_id]
            if spec:
                return spec
        return [r for r in rows if r.get("line_id") is None] or rows

    # 1) Tarihe özel istisna
    ovr = _line_filter([o for o in _state.get("overrides", [])
                        if o["override_date"] == the_date])
    if ovr:
        scoped = _line_filter(_state["shifts"])
        general = [s for s in scoped if s.get("day_of_week") is None]
        day_specific = [s for s in scoped if s.get("day_of_week") == pg_dow]
        # Aynı kod genel ve güne özel satırda bulunabilir. Önce geneli,
        # sonra o günün satırını koyarak override için doğru tabanı seç.
        base = {s["code"]: dict(s) for s in general}
        base.update({s["code"]: dict(s) for s in day_specific})
        out = []
        for o in ovr:
            row = base.get(o["code"], dict(_FALLBACK_SHIFT)).copy()
            row.update({k: v for k, v in o.items()
                        if v is not None and k != "override_date"})
            row.setdefault("display_order", 1)
            row["is_active"] = True
            out.append(row)
        return out

    # 2) Güne özel kural (Cumartesi vb.)
    day_rows = _line_filter([s for s in _state["shifts"]
                             if s.get("day_of_week") == pg_dow])
    if day_rows:
        return day_rows

    # 3) Global varsayılan
    return _line_filter([s for s in _state["shifts"]
                         if s.get("day_of_week") is None]) or list(_state["shifts"])

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
    shifts = get_shifts(line_id=line_id, dt=dt)
    for s in shifts:
        if _in_window(h, s["start_hour"], s["end_hour"]):
            return s["code"], s["label"]
    first = shifts[0]
    return first["code"], first["label"]


def detect_shift_code(dt: datetime = None, line_id: int = None) -> str:
    return detect_shift(dt, line_id)[0]


def shift_by_code(code: str, line_id: int = None, dt=None):
    for s in get_shifts(line_id=line_id, dt=dt):
        if s["code"] == code:
            return s
    return get_shifts(line_id=line_id, dt=dt)[0]


def shift_date_for_datetime(shift: dict, dt: datetime) -> date:
    """``dt`` anının ait olduğu vardiya tarihini döndür.

    Gece yarısını geçen bir vardiyada (ör. 22:00–06:00) 02:00 anı bir
    önceki takvim gününde başlayan vardiyaya aittir. Normal vardiyalarda
    tarih doğrudan ``dt.date()`` olur.
    """
    start_hour = int(shift["start_hour"])
    end_hour = int(shift["end_hour"])
    crosses_midnight = end_hour <= start_hour
    if crosses_midnight and dt.hour < end_hour:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def shift_end_datetime(shift: dict, shift_date: date, tzinfo=None) -> datetime:
    """DB vardiya tanımından kesin bitiş zamanını üret.

    ``end_hour=24`` ve gece yarısını geçen vardiyalar desteklenir. Bu
    fonksiyon sabit hafta içi/cumartesi saati içermez; kendisine verilen
    satır ``get_shifts``/``shift_by_code`` ile DB'den çözülmelidir.
    """
    start_hour = int(shift["start_hour"])
    end_hour = int(shift["end_hour"])
    start_at = datetime.combine(shift_date, datetime.min.time(), tzinfo=tzinfo)
    start_at += timedelta(hours=start_hour)
    end_at = datetime.combine(shift_date, datetime.min.time(), tzinfo=tzinfo)
    end_at += timedelta(hours=end_hour)
    if end_at <= start_at:
        end_at += timedelta(days=1)
    return end_at


def shift_summary_datetime(shift: dict, shift_date: date, tzinfo=None) -> datetime:
    """Bir vardiya tarihinin özet yazma anını (latest_end) döndür."""
    start_hour = int(shift["start_hour"])
    latest_end = int(shift["latest_end"])
    summary_at = datetime.combine(shift_date, datetime.min.time(), tzinfo=tzinfo)
    summary_at += timedelta(hours=latest_end)
    start_at = datetime.combine(shift_date, datetime.min.time(), tzinfo=tzinfo)
    start_at += timedelta(hours=start_hour)
    if summary_at <= start_at:
        summary_at += timedelta(days=1)
    return summary_at


def planned_seconds(code: str, line_id: int = None, dt=None) -> int:
    return int(shift_by_code(code, line_id, dt).get("planned_seconds")
               or _FALLBACK_SHIFT["planned_seconds"])


def resolve_shift_date(shift: dict, now: datetime):
    """Vardiya şu an bitmiş mi? Bittiyse hangi shift_date'e ait, değilse None.
    (production_logger'daki mantığın taşınmış hali — Adım 4'te oraya bağlanacak.)"""
    today = now.date()
    today_summary = shift_summary_datetime(shift, today, now.tzinfo)
    if now >= today_summary:
        return today

    previous = today - timedelta(days=1)
    previous_summary = shift_summary_datetime(shift, previous, now.tzinfo)
    if now >= previous_summary:
        return previous
    return None
