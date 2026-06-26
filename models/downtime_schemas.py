"""
Duruş Takip — Pydantic Şemaları
schemas/downtime_schemas.py
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ─── DURUŞ SEBEBİ ─────────────────────────────
class ReasonResponse(BaseModel):
    reason_id: int
    reason_code: str
    reason_name: str
    category: str
    color_hex: str
    icon: str
    display_order: int

    class Config:
        from_attributes = True


# ─── DURUŞ BAŞLAT ─────────────────────────────
class DowntimeStartRequest(BaseModel):
    line_id: int
    reason_id: int
    machine_id: Optional[int] = None
    operator_name: Optional[str] = None
    shift: Optional[str] = None
    notes: Optional[str] = None
    trigger: Optional[str] = None   # "manual", "empty_line", "stall" gibi
    started_at: Optional[datetime] = None  # monitor gerçek duruş anını gönderir; boşsa API now()

class DowntimeStartResponse(BaseModel):
    downtime_id: int
    line_id: int
    reason_id: int
    reason_name: str
    started_at: datetime
    is_active: bool
    message: str = "Duruş başlatıldı"
    

    class Config:
        from_attributes = True


# ─── DURUŞ BİTİR ──────────────────────────────
class DowntimeStopRequest(BaseModel):
    notes: Optional[str] = None


class DowntimeStopResponse(BaseModel):
    downtime_id: int
    line_id: int
    reason_name: str
    started_at: datetime
    ended_at: datetime
    duration_sec: int
    duration_text: str          # "12 dk 34 sn" gibi okunabilir format
    message: str = "Duruş sonlandırıldı"

    class Config:
        from_attributes = True


# ─── AKTİF DURUŞ BİLGİSİ ─────────────────────
class ActiveDowntimeResponse(BaseModel):
    downtime_id: int
    line_id: int
    line_name: str
    machine_id: Optional[int] = None
    machine_name: Optional[str] = None
    reason_id: int
    reason_code: str
    reason_name: str
    color_hex: str
    icon: str
    operator_name: Optional[str] = None
    shift: Optional[str] = None
    started_at: datetime
    elapsed_sec: int            # Şu ana kadar geçen süre
    elapsed_text: str           # "12 dk 34 sn"
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# ─── GEÇMİŞ KAYITLAR ─────────────────────────
class DowntimeHistoryItem(BaseModel):
    downtime_id: int
    line_name: str
    machine_name: Optional[str] = None
    reason_code: str
    reason_name: str
    category: str
    color_hex: str
    operator_name: Optional[str] = None
    shift: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_sec: Optional[int] = None
    duration_text: Optional[str] = None
    notes: Optional[str] = None
    

    class Config:
        from_attributes = True


class DowntimeHistoryResponse(BaseModel):
    items: list[DowntimeHistoryItem]
    total_count: int
    total_duration_sec: int
    total_duration_text: str