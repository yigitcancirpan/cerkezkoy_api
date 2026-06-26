"""
Duruş Takip ORM Modelleri
Bu dosyayı models/orm_models.py'nin SONUNA ekle
veya ayrı import olarak kullan.

Mevcut modellere ekleme:
  - DowntimeReason  (downtime_reasons tablosu)
  - Downtime        (downtimes tablosu)
"""

from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean,
    DateTime, Text, ForeignKey, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from models.database import Base


class DowntimeReason(Base):
    """Duruş sebepleri — lookup tablosu"""
    __tablename__ = "downtime_reasons"

    reason_id     = Column(Integer, primary_key=True, autoincrement=True)
    reason_code   = Column(String(20), unique=True, nullable=False)
    reason_name   = Column(String(100), nullable=False)
    category      = Column(String(50), default="planned")     # planned / unplanned
    color_hex     = Column(String(7), default="#EF4444")
    icon          = Column(String(50), default="wrench")
    display_order = Column(Integer, default=0)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    # İlişki
    downtimes = relationship("Downtime", back_populates="reason")


class Downtime(Base):
    """Duruş kayıtları"""
    __tablename__ = "downtimes"

    downtime_id   = Column(BigInteger, primary_key=True, autoincrement=True)
    line_id       = Column(Integer, ForeignKey("production_lines.line_id"), nullable=False)
    machine_id    = Column(Integer, ForeignKey("machines.machine_id"), nullable=True)
    reason_id     = Column(Integer, ForeignKey("downtime_reasons.reason_id"), nullable=False)
    operator_name = Column(String(100))
    shift         = Column(String(20))
    started_at    = Column(DateTime(timezone=True), server_default=func.now())
    ended_at      = Column(DateTime(timezone=True), nullable=True)
    duration_sec  = Column(Integer, nullable=True)
    notes         = Column(Text, nullable=True)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now())
    trigger       = Column(String(20), nullable=True)  # "manual", "empty_line", "stall" gibi

    # İlişkiler
    reason          = relationship("DowntimeReason", back_populates="downtimes")
    production_line = relationship("ProductionLine")
    machine         = relationship("Machine")

    # İndeksler
    __table_args__ = (
        Index("idx_downtimes_line_active", "line_id", "is_active",
              postgresql_where=(is_active == True)),
        Index("idx_downtimes_started", "started_at"),
        Index("idx_downtimes_line_date", "line_id", "started_at"),
    )