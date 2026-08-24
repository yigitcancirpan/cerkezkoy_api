"""
Duruş Takip API Router
routers/downtimes.py

Endpoint'ler:
  GET   /api/v1/downtimes/reasons         → Duruş sebeplerini listele
  POST  /api/v1/downtimes/start           → Duruş başlat (timer başlar)
  POST  /api/v1/downtimes/{id}/stop       → Duruş bitir (timer durur, süre hesaplanır)
  PATCH /api/v1/downtimes/{id}/reason     → Duruş sebebini güncelle (oto→gerçek)
  GET   /api/v1/downtimes/active          → Aktif duruşları getir
  GET   /api/v1/downtimes/active/{line_id}→ Belirli hattın aktif duruşu
  GET   /api/v1/downtimes/history         → Geçmiş duruşlar (filtreli)
  GET   /api/v1/downtimes/monitor/status  → Monitor servis durumu
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from models.database import get_db
from models.downtime_models import Downtime, DowntimeReason
from models.orm_models import ProductionLine, Machine
from models.downtime_schemas import (
    ReasonResponse,
    DowntimeStartRequest, DowntimeStartResponse,
    DowntimeStopRequest, DowntimeStopResponse,
    ActiveDowntimeResponse,
    DowntimeHistoryItem, DowntimeHistoryResponse,
)

router = APIRouter(prefix="/api/v1/downtimes", tags=["Duruş Takip"])
TZ = ZoneInfo("Europe/Istanbul")


# ─── YARDIMCI FONKSİYONLAR ────────────────────
def _format_duration(seconds: int) -> str:
    """Saniyeyi okunabilir Türkçe formata çevir"""
    if seconds < 60:
        return f"{seconds} sn"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} dk {secs} sn"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} sa {minutes} dk"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _close_with_break_segments(
    db: Session,
    downtime: Downtime,
    ended_at: datetime,
    closing_note: Optional[str] = None,
):
    """Kapalı duruşu gerçek mola pencerelerinde ayrı kayıtlara böler."""
    import shift_utils
    from services.break_service import split_interval_by_breaks

    started_at = downtime.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    local_start = started_at.astimezone(TZ)
    shift_code = downtime.shift or shift_utils.detect_shift_code(
        local_start,
        line_id=downtime.line_id,
    )
    shift = shift_utils.shift_by_code(
        shift_code,
        line_id=downtime.line_id,
        dt=local_start,
    )
    shift_date = shift_utils.shift_date_for_datetime(shift, local_start)
    windows = shift_utils.break_windows(
        shift_code,
        shift_date,
        line_id=downtime.line_id,
        tzinfo=TZ,
    )
    segments = split_interval_by_breaks(started_at, ended_at, windows)
    if not segments:
        segments = [{"start": started_at, "end": ended_at, "is_break": False}]
    has_break_overlap = any(item["is_break"] for item in segments)

    operator_reason = db.query(DowntimeReason).filter(
        DowntimeReason.reason_code == "OPERATOR"
    ).first()
    if has_break_overlap and not operator_reason:
        raise HTTPException(500, "OPERATOR mola sebebi tanımlı değil")

    original_reason = downtime.reason
    original_trigger = downtime.trigger
    outside_reason = original_reason
    if original_reason.reason_code == "OPERATOR":
        outside_reason = db.query(DowntimeReason).filter(
            DowntimeReason.reason_code == "BELIRLENMEDI"
        ).first() or original_reason

    base_notes = downtime.notes or ""
    if closing_note:
        base_notes += f"\n[Kapanış] {closing_note}"
    created = []
    for index, segment in enumerate(segments):
        reason = operator_reason if segment["is_break"] else outside_reason
        segment_note = base_notes
        if has_break_overlap:
            segment_note += (
                "\n[Otomatik bölüm] Tanımlı mola penceresi"
                if segment["is_break"]
                else "\n[Otomatik bölüm] Mola penceresi dışı"
            )
        target = downtime if index == 0 else Downtime(
            line_id=downtime.line_id,
            machine_id=downtime.machine_id,
            operator_name=downtime.operator_name,
            shift=shift_code,
            created_at=downtime.created_at,
        )
        target.reason_id = reason.reason_id
        target.started_at = segment["start"]
        target.ended_at = segment["end"]
        target.duration_sec = int(
            (segment["end"] - segment["start"]).total_seconds()
        )
        target.notes = segment_note
        target.is_active = False
        target.updated_at = _now_utc()
        target.trigger = "break_window" if segment["is_break"] else original_trigger
        if index > 0:
            db.add(target)
        created.append(target)
    return created


def _classified_segments(downtime: Downtime, ended_at: datetime, operator_reason):
    """Kayıtlı duruşu analiz için mola/mola-dışı parçalara ayırır."""
    import shift_utils
    from services.break_service import split_interval_by_breaks

    started_at = downtime.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    local_start = started_at.astimezone(TZ)
    shift_code = downtime.shift or shift_utils.detect_shift_code(
        local_start,
        line_id=downtime.line_id,
    )
    shift = shift_utils.shift_by_code(
        shift_code,
        line_id=downtime.line_id,
        dt=local_start,
    )
    shift_date = shift_utils.shift_date_for_datetime(shift, local_start)
    windows = shift_utils.break_windows(
        shift_code,
        shift_date,
        line_id=downtime.line_id,
        tzinfo=TZ,
    )
    raw = split_interval_by_breaks(started_at, ended_at, windows)
    result = []
    for index, segment in enumerate(raw):
        if segment["is_break"]:
            reason = operator_reason
            code = reason.reason_code
            name = reason.reason_name
            category = "planned"
            color = reason.color_hex
        else:
            reason = downtime.reason
            code = reason.reason_code
            name = reason.reason_name
            color = reason.color_hex
            category = "planned" if (
                reason.exclude_from_oee and code != "OPERATOR"
            ) else "unplanned"
            if code == "OPERATOR":
                code = "OPERATOR_EXTRA"
                name = "Mola Dışı Operatör Duruşu"
                color = "#F59E0B"
                category = "unplanned"
        result.append({
            "segment_index": index,
            "reason_code": code,
            "reason_name": name,
            "category": category,
            "color_hex": color,
            "started_at": segment["start"],
            "ended_at": segment["end"],
            "duration_sec": int(
                (segment["end"] - segment["start"]).total_seconds()
            ),
            "is_break_segment": segment["is_break"],
        })
    return result


# ─── 1) DURUŞ SEBEPLERİ ───────────────────────
@router.get("/reasons", response_model=list[ReasonResponse])
def get_reasons(db: Session = Depends(get_db)):
    """Aktif duruş sebeplerini display_order sırasına göre getir"""
    reasons = (
        db.query(DowntimeReason)
        .filter(DowntimeReason.is_active == True)
        .order_by(DowntimeReason.display_order)
        .all()
    )
    return reasons


# ─── 2) DURUŞ BAŞLAT ──────────────────────────
@router.post("/start", response_model=DowntimeStartResponse, status_code=201)
def start_downtime(req: DowntimeStartRequest, db: Session = Depends(get_db)):
    """
    Yeni duruş kaydı başlat.
    Aynı hat için zaten aktif duruş varsa hata döner.
    """
    # Hat kontrolü
    line = db.query(ProductionLine).filter(
        ProductionLine.line_id == req.line_id
    ).first()
    if not line:
        raise HTTPException(404, f"Hat bulunamadı: line_id={req.line_id}")

    # Sebep kontrolü
    reason = db.query(DowntimeReason).filter(
        DowntimeReason.reason_id == req.reason_id
    ).first()
    if not reason:
        raise HTTPException(404, f"Sebep bulunamadı: reason_id={req.reason_id}")

    # Aynı hatta aktif duruş var mı?
    existing = db.query(Downtime).filter(
        Downtime.line_id == req.line_id,
        Downtime.is_active == True
    ).first()
    if existing:
        raise HTTPException(
            409,
            f"Bu hatta zaten aktif bir duruş var (ID: {existing.downtime_id}, "
            f"Sebep: {existing.reason.reason_name})"
        )

    # Makine kontrolü (opsiyonel)
    if req.machine_id:
        machine = db.query(Machine).filter(
            Machine.machine_id == req.machine_id
        ).first()
        if not machine:
            raise HTTPException(404, f"Makine bulunamadı: machine_id={req.machine_id}")

    # Duruş oluştur
    # started_at: monitor gerçek duruş anını (stop_detected_at) gönderirse onu kullan,
    # böylece grace period (180sn) süresi duruşa dahil olur. Gelmezse API "şimdi" basar.
    started = req.started_at or _now_utc()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    _now = _now_utc()
    if started > _now or (_now - started) > timedelta(hours=2):
        started = _now   # absürt/gelecek değer geldiyse korun

    downtime = Downtime(
        line_id=req.line_id,
        machine_id=req.machine_id,
        reason_id=req.reason_id,
        operator_name=req.operator_name,
        shift=req.shift,
        notes=req.notes,
        started_at=started,
        is_active=True,
        trigger=req.trigger,
    )
    db.add(downtime)
    db.commit()
    db.refresh(downtime)

    return DowntimeStartResponse(
        downtime_id=downtime.downtime_id,
        line_id=downtime.line_id,
        reason_id=downtime.reason_id,
        reason_name=reason.reason_name,
        started_at=downtime.started_at,
        is_active=True,
        message=f"Duruş başlatıldı — {reason.reason_name}",
    )


# ─── 3) DURUŞ BİTİR ──────────────────────────
@router.post("/{downtime_id}/stop", response_model=DowntimeStopResponse)
def stop_downtime(
    downtime_id: int,
    req: DowntimeStopRequest = DowntimeStopRequest(),
    db: Session = Depends(get_db),
):
    """Aktif duruşu sonlandır, süreyi hesapla"""
    downtime = db.query(Downtime).filter(
        Downtime.downtime_id == downtime_id,
        Downtime.is_active == True
    ).first()

    if not downtime:
        raise HTTPException(404, "Aktif duruş bulunamadı")

    request_now = _now_utc()
    ended_at = req.ended_at or request_now
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)

    started_at = downtime.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    # Geçmiş vardiya sonuna geri tarihlemek geçerlidir; fakat başlangıçtan
    # önce veya gelecekte bir bitiş veri bütünlüğünü bozar.
    if ended_at < started_at:
        raise HTTPException(422, "Bitiş zamanı başlangıç zamanından önce olamaz")
    if ended_at > request_now + timedelta(seconds=5):
        raise HTTPException(422, "Bitiş zamanı gelecekte olamaz")

    duration = int((ended_at - started_at).total_seconds())
    segments = _close_with_break_segments(
        db,
        downtime,
        ended_at,
        closing_note=req.notes,
    )

    db.commit()
    db.refresh(downtime)

    return DowntimeStopResponse(
        downtime_id=downtime.downtime_id,
        line_id=downtime.line_id,
        reason_name=downtime.reason.reason_name,
        started_at=downtime.started_at,
        ended_at=downtime.ended_at,
        duration_sec=duration,
        duration_text=_format_duration(duration),
        message=(
            f"Duruş sonlandırıldı — {_format_duration(duration)}"
            + (f" ({len(segments)} bölüme ayrıldı)" if len(segments) > 1 else "")
        ),
    )


# ─── 3b) SEBEP GÜNCELLE (oto duruş → gerçek sebep) ──
@router.patch("/{downtime_id}/reason")
def update_downtime_reason(
    downtime_id: int,
    reason_id: int = Query(..., description="Yeni sebep ID"),
    operator_name: Optional[str] = Query(None, description="Operatör adı"),
    db: Session = Depends(get_db),
):
    """
    Otomatik oluşturulan duruşun sebebini güncelle.
    Operatör tablet'ten gerçek sebebi seçtiğinde çağrılır.
    """
    downtime = db.query(Downtime).filter(
        Downtime.downtime_id == downtime_id,
    ).first()

    if not downtime:
        raise HTTPException(404, "Duruş bulunamadı")

    reason = db.query(DowntimeReason).filter(
        DowntimeReason.reason_id == reason_id
    ).first()

    if not reason:
        raise HTTPException(404, f"Sebep bulunamadı: reason_id={reason_id}")

    old_reason = downtime.reason.reason_name
    downtime.reason_id = reason_id
    downtime.updated_at = _now_utc()

    if operator_name:
        downtime.operator_name = operator_name

    # Not ekle
    downtime.notes = (
        (downtime.notes or "")
        + f"\n[Sebep güncellendi] {old_reason} → {reason.reason_name}"
    )

    db.commit()
    db.refresh(downtime)

    return {
        "downtime_id": downtime.downtime_id,
        "old_reason": old_reason,
        "new_reason": reason.reason_name,
        "message": f"Sebep güncellendi: {reason.reason_name}",
    }


# ─── 4) AKTİF DURUŞLAR ───────────────────────
@router.get("/active", response_model=list[ActiveDowntimeResponse])
def get_active_downtimes(db: Session = Depends(get_db)):
    """Tüm hatlardaki aktif duruşları getir"""
    actives = (
        db.query(Downtime)
        .filter(Downtime.is_active == True)
        .order_by(Downtime.started_at)
        .all()
    )
    now = _now_utc()
    result = []
    for d in actives:
        elapsed = int((now - d.started_at).total_seconds())
        result.append(ActiveDowntimeResponse(
            downtime_id=d.downtime_id,
            line_id=d.line_id,
            line_name=d.production_line.line_name if d.production_line else "?",
            machine_id=d.machine_id,
            machine_name=d.machine.machine_name if d.machine else None,
            reason_id=d.reason_id,
            reason_code=d.reason.reason_code,
            reason_name=d.reason.reason_name,
            color_hex=d.reason.color_hex,
            icon=d.reason.icon,
            operator_name=d.operator_name,
            shift=d.shift,
            started_at=d.started_at,
            elapsed_sec=elapsed,
            elapsed_text=_format_duration(elapsed),
            notes=d.notes,
        ))
    return result


@router.get("/active/{line_id}", response_model=Optional[ActiveDowntimeResponse])
def get_active_downtime_for_line(line_id: int, db: Session = Depends(get_db)):
    """Belirli bir hattın aktif duruşunu getir (yoksa null)"""
    d = db.query(Downtime).filter(
        Downtime.line_id == line_id,
        Downtime.is_active == True
    ).first()

    if not d:
        return None

    now = _now_utc()
    elapsed = int((now - d.started_at).total_seconds())
    return ActiveDowntimeResponse(
        downtime_id=d.downtime_id,
        line_id=d.line_id,
        line_name=d.production_line.line_name if d.production_line else "?",
        machine_id=d.machine_id,
        machine_name=d.machine.machine_name if d.machine else None,
        reason_id=d.reason_id,
        reason_code=d.reason.reason_code,
        reason_name=d.reason.reason_name,
        color_hex=d.reason.color_hex,
        icon=d.reason.icon,
        operator_name=d.operator_name,
        shift=d.shift,
        started_at=d.started_at,
        elapsed_sec=elapsed,
        elapsed_text=_format_duration(elapsed),
        notes=d.notes,
    )


# ─── 5) GEÇMİŞ KAYITLAR ──────────────────────
@router.get("/history", response_model=DowntimeHistoryResponse)
def get_downtime_history(
    line_id: Optional[int] = Query(None, description="Hat filtresi"),
    reason_id: Optional[int] = Query(None, description="Sebep filtresi"),
    date_from: Optional[str] = Query(None, description="Başlangıç tarihi (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Bitiş tarihi (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Duruş geçmişini filtreli getir"""
    query = db.query(Downtime).filter(Downtime.is_active == False)

    if line_id:
        query = query.filter(Downtime.line_id == line_id)
    if reason_id:
        query = query.filter(Downtime.reason_id == reason_id)
    if date_from:
        query = query.filter(Downtime.started_at >= date_from)
    if date_to:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(Downtime.started_at < dt_to)

    total_count = query.count()

    # Toplam süre
    all_durations = [d.duration_sec for d in query.all() if d.duration_sec]
    total_sec = sum(all_durations)

    # Sayfalı sonuç
    records = (
        query
        .order_by(desc(Downtime.started_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for d in records:
        items.append(DowntimeHistoryItem(
            downtime_id=d.downtime_id,
            line_name=d.production_line.line_name if d.production_line else "?",
            machine_name=d.machine.machine_name if d.machine else None,
            reason_code=d.reason.reason_code,
            reason_name=d.reason.reason_name,
            category=d.reason.category,
            color_hex=d.reason.color_hex,
            operator_name=d.operator_name,
            shift=d.shift,
            started_at=d.started_at,
            ended_at=d.ended_at,
            duration_sec=d.duration_sec,
            duration_text=_format_duration(d.duration_sec) if d.duration_sec else None,
            notes=d.notes,
        ))

    return DowntimeHistoryResponse(
        items=items,
        total_count=total_count,
        total_duration_sec=total_sec,
        total_duration_text=_format_duration(total_sec),
    )

@router.get("/analysis")
def downtime_analysis(
    line_id: Optional[int] = Query(None, description="Hat filtresi"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    shift: Optional[str] = Query(None, description="vardiya_1 / vardiya_2 ..."),
    db: Session = Depends(get_db),
):
    """
    Sebep bazlı duruş analizi: Pareto (süre), planlı/plansız, günlük trend.
    Kayıtlar tanımlı mola pencerelerinde sanal olarak bölünür; böylece eski
    birleşik duruşlar da yeni kuralla doğru görünür.
    """
    base = db.query(Downtime).join(
        DowntimeReason, Downtime.reason_id == DowntimeReason.reason_id
    ).filter(
        Downtime.is_active == False,
        Downtime.duration_sec.isnot(None),
    )
 
    if line_id:
        base = base.filter(Downtime.line_id == line_id)
    if shift:
        base = base.filter(Downtime.shift == shift)
    if date_from:
        base = base.filter(Downtime.started_at >= date_from)
    if date_to:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        base = base.filter(Downtime.started_at < dt_to)
 
    operator_reason = db.query(DowntimeReason).filter(
        DowntimeReason.reason_code == "OPERATOR"
    ).first()
    if not operator_reason:
        raise HTTPException(500, "OPERATOR mola sebebi tanımlı değil")

    reason_map = {}
    daily_map = {}
    for downtime in base.order_by(Downtime.started_at).all():
        for segment in _classified_segments(
            downtime,
            downtime.ended_at,
            operator_reason,
        ):
            code = segment["reason_code"]
            item = reason_map.setdefault(code, {
                "reason_code": code,
                "reason_name": segment["reason_name"],
                "category": segment["category"],
                "color_hex": segment["color_hex"],
                "count": 0,
                "total_sec": 0,
            })
            item["count"] += 1
            item["total_sec"] += segment["duration_sec"]
            day = str(segment["started_at"].astimezone(TZ).date())
            daily_map.setdefault(day, {}).setdefault(code, 0)
            daily_map[day][code] += segment["duration_sec"]

    reasons = list(reason_map.values())
    for item in reasons:
        item["avg_sec"] = int(item["total_sec"] / item["count"])
    reasons.sort(key=lambda item: item["total_sec"], reverse=True)

    daily = [
        {"date": ds, "reasons": daily_map[ds], "total_sec": sum(daily_map[ds].values())}
        for ds in sorted(daily_map.keys())
    ]
 
    total_sec = sum(r["total_sec"] for r in reasons)
    total_cnt = sum(r["count"] for r in reasons)
    planned_sec = sum(
        r["total_sec"] for r in reasons if r["category"] == "planned"
    )
 
    return {
        "filters": {"line_id": line_id, "date_from": date_from, "date_to": date_to, "shift": shift},
        "total_sec": total_sec,
        "total_count": total_cnt,
        "planned_sec": planned_sec,
        "unplanned_sec": total_sec - planned_sec,
        "reasons": reasons,
        "daily": daily,
        "reason_meta": {
            r["reason_code"]: {"name": r["reason_name"], "color": r["color_hex"], "category": r["category"]}
            for r in reasons
        },

    }
@router.get("/timeline")
def downtime_timeline(
    line_id: Optional[int] = Query(None, description="Hat filtresi"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    shift: Optional[str] = Query(None, description="vardiya_1 / vardiya_2 ..."),
    db: Session = Depends(get_db),
):
    """
    Zaman çizelgesi: aralıktaki duruşları tek tek, kronolojik döner.
    Hangi saatte hangi duruş olmuş — başlangıç/bitiş/süre/sebep.
    Devam eden (kapanmamış) duruş varsa bitiş = şu an.
    """
    q = db.query(Downtime).join(
        DowntimeReason, Downtime.reason_id == DowntimeReason.reason_id
    )
    if line_id:
        q = q.filter(Downtime.line_id == line_id)
    if shift:
        q = q.filter(Downtime.shift == shift)
    if date_from:
        q = q.filter(Downtime.started_at >= date_from)
    if date_to:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        q = q.filter(Downtime.started_at < dt_to)

    rows = q.order_by(Downtime.started_at).all()
    now = _now_utc()
    operator_reason = db.query(DowntimeReason).filter(
        DowntimeReason.reason_code == "OPERATOR"
    ).first()
    if not operator_reason:
        raise HTTPException(500, "OPERATOR mola sebebi tanımlı değil")

    events = []
    for d in rows:
        if d.ended_at is None:
            ended = now
            active = True
        else:
            ended = d.ended_at
            active = False
        segments = _classified_segments(d, ended, operator_reason)
        for index, segment in enumerate(segments):
            events.append({
                "downtime_id": d.downtime_id,
                "segment_key": f"{d.downtime_id}:{index}",
                "reason_code": segment["reason_code"],
                "reason_name": segment["reason_name"],
                "category": segment["category"],
                "color_hex": segment["color_hex"],
                "shift": d.shift,
                "operator_name": d.operator_name,
                "started_at": segment["started_at"],
                "ended_at": segment["ended_at"],
                "duration_sec": segment["duration_sec"],
                "is_active": active and index == len(segments) - 1,
                "is_break_segment": segment["is_break_segment"],
                "notes": d.notes,
            })

    return {
        "filters": {"line_id": line_id, "date_from": date_from, "date_to": date_to, "shift": shift},
        "count": len(events),
        "events": events,
    }
    # ════════════════════════════════════════════════
#  YÖNETİM PANELİ — Sebep yönetimi + kayıt düzeltme
# ════════════════════════════════════════════════

# Monitor + SQL bu kodları STRING olarak eşleştiriyor → kod kilitli, pasifleştirilemez
PROTECTED_DOWNTIME_CODES = {
    "BELIRLENMEDI", "VARDIYA_SONU", "BOSALTMA", "OPERATOR",
}


def _shift_for(dt: datetime, line_id: int) -> str:
    import shift_utils
    return shift_utils.detect_shift_code(dt.astimezone(TZ), line_id=line_id)


class ReasonUpdate(BaseModel):
    reason_name: Optional[str] = None
    category: Optional[str] = None
    color_hex: Optional[str] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class ReasonCreate(BaseModel):
    reason_code: str
    reason_name: str
    category: str = "unplanned"
    color_hex: str = "#EF4444"
    icon: str = "wrench"
    display_order: int = 0


class ManualDowntime(BaseModel):
    line_id: int = 1
    reason_id: int
    started_at: datetime
    ended_at: Optional[datetime] = None
    shift: Optional[str] = None
    operator_name: Optional[str] = None
    notes: Optional[str] = None


@router.get("/reasons/manage")
def list_reasons_manage(db: Session = Depends(get_db)):
    """Panel için: pasifler dahil tüm sebepler + is_active + protected bayrağı."""
    rows = db.query(DowntimeReason).order_by(
        DowntimeReason.display_order, DowntimeReason.reason_id
    ).all()
    return [{
        "reason_id": r.reason_id, "reason_code": r.reason_code,
        "reason_name": r.reason_name, "category": r.category,
        "color_hex": r.color_hex, "icon": r.icon,
        "display_order": r.display_order, "is_active": r.is_active,
        "protected": r.reason_code in PROTECTED_DOWNTIME_CODES,
    } for r in rows]


@router.post("/reasons", status_code=201)
def create_reason(req: ReasonCreate, db: Session = Depends(get_db)):
    code = req.reason_code.strip().upper()
    if not code:
        raise HTTPException(400, "Kod boş olamaz")
    if db.query(DowntimeReason).filter(DowntimeReason.reason_code == code).first():
        raise HTTPException(409, f"Bu kod zaten var: {code}")
    r = DowntimeReason(
        reason_code=code, reason_name=req.reason_name, category=req.category,
        color_hex=req.color_hex, icon=req.icon,
        display_order=req.display_order, is_active=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    return {"reason_id": r.reason_id, "reason_code": r.reason_code, "message": "Sebep eklendi"}


@router.patch("/reasons/{reason_id}")
def update_reason(reason_id: int, req: ReasonUpdate, db: Session = Depends(get_db)):
    r = db.query(DowntimeReason).filter(DowntimeReason.reason_id == reason_id).first()
    if not r:
        raise HTTPException(404, "Sebep bulunamadı")
    # reason_code BİLEREK değiştirilemez (monitor/SQL string eşleşmesi buna bağlı)
    if req.is_active is False and r.reason_code in PROTECTED_DOWNTIME_CODES:
        raise HTTPException(409, f"'{r.reason_code}' sistem sebebi — pasifleştirilemez")
    for f in ("reason_name", "category", "color_hex", "icon", "display_order", "is_active"):
        v = getattr(req, f)
        if v is not None:
            setattr(r, f, v)
    db.commit()
    return {"reason_id": r.reason_id, "message": "Güncellendi"}


@router.post("/manual", status_code=201)
def create_manual_downtime(req: ManualDowntime, db: Session = Depends(get_db)):
    """Panelden elle duruş kaydı — kaçmış/yanlış kayıtları telafi için."""
    reason = db.query(DowntimeReason).filter(DowntimeReason.reason_id == req.reason_id).first()
    if not reason:
        raise HTTPException(404, "Sebep bulunamadı")

    start = req.started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    shift = req.shift or _shift_for(start, req.line_id)
    note = (req.notes or "") + " [manuel kayıt]"

    if req.ended_at is not None:
        end = req.ended_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            raise HTTPException(400, "Bitiş, başlangıçtan sonra olmalı")
        d = Downtime(
            line_id=req.line_id, reason_id=req.reason_id, shift=shift,
            operator_name=req.operator_name, notes=note,
            started_at=start, is_active=True, trigger="manual",
        )
        db.add(d)
        db.flush()
        segments = _close_with_break_segments(db, d, end)
    else:
        if db.query(Downtime).filter(Downtime.line_id == req.line_id,
                                     Downtime.is_active == True).first():
            raise HTTPException(409, "Bu hatta zaten aktif duruş var")
        d = Downtime(
            line_id=req.line_id, reason_id=req.reason_id, shift=shift,
            operator_name=req.operator_name, notes=note,
            started_at=start, is_active=True, trigger="manual",
        )
        db.add(d)
    db.commit(); db.refresh(d)
    return {
        "downtime_id": d.downtime_id,
        "segment_count": len(segments) if req.ended_at is not None else 1,
        "message": "Manuel duruş eklendi",
    }


@router.delete("/{downtime_id}")
def delete_downtime(downtime_id: int, db: Session = Depends(get_db)):
    d = db.query(Downtime).filter(Downtime.downtime_id == downtime_id).first()
    if not d:
        raise HTTPException(404, "Duruş bulunamadı")
    db.delete(d); db.commit()
    return {"deleted": downtime_id}

class DowntimeEdit(BaseModel):
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    reason_id: Optional[int] = None
    notes: Optional[str] = None


@router.patch("/{downtime_id}/edit")
def edit_downtime(downtime_id: int, req: DowntimeEdit, db: Session = Depends(get_db)):
    """Duruşun saatini/sebebini elle düzelt. duration_sec yeniden hesaplanır."""
    d = db.query(Downtime).filter(Downtime.downtime_id == downtime_id).first()
    if not d:
        raise HTTPException(404, "Duruş bulunamadı")

    if req.reason_id is not None:
        reason = db.query(DowntimeReason).filter(DowntimeReason.reason_id == req.reason_id).first()
        if not reason:
            raise HTTPException(404, "Sebep bulunamadı")
        d.reason_id = req.reason_id

    if req.started_at is not None:
        st = req.started_at
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        d.started_at = st

    if req.ended_at is not None:
        en = req.ended_at
        if en.tzinfo is None:
            en = en.replace(tzinfo=timezone.utc)
        d.ended_at = en
        d.is_active = False          # bitiş verildiyse kapalı say

    # Süreyi yeniden hesapla (kapalıysa)
    if d.ended_at is not None:
        if d.ended_at <= d.started_at:
            raise HTTPException(400, "Bitiş, başlangıçtan sonra olmalı")
        d.duration_sec = int((d.ended_at - d.started_at).total_seconds())

    if req.notes:
        d.notes = (d.notes or "") + f"\n[Düzenlendi] {req.notes}"
    d.updated_at = _now_utc()

    db.commit()
    return {"downtime_id": d.downtime_id, "duration_sec": d.duration_sec, "message": "Güncellendi"}
