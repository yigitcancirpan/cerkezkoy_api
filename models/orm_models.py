from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean,
    DateTime, Date, Text, ForeignKey, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from models.database import Base


class ProductionLine(Base):
    __tablename__ = "production_lines"
    line_id = Column(Integer, primary_key=True, autoincrement=True)
    line_name = Column(String(100), nullable=False)
    location = Column(String(50), nullable=False)
    description = Column(String(500))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    machines = relationship("Machine", back_populates="production_line")


class Machine(Base):
    __tablename__ = "machines"
    machine_id = Column(Integer, primary_key=True, autoincrement=True)
    line_id = Column(Integer, ForeignKey("production_lines.line_id"), nullable=False)
    machine_name = Column(String(100), nullable=False)
    machine_type = Column(String(50))
    manufacturer = Column(String(100))
    model = Column(String(100))
    serial_number = Column(String(100))
    install_date = Column(Date)
    status = Column(String(20), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    production_line = relationship("ProductionLine", back_populates="machines")
    sensors = relationship("Sensor", back_populates="machine")


class Sensor(Base):
    __tablename__ = "sensors"
    sensor_id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, ForeignKey("machines.machine_id"), nullable=False)
    sensor_name = Column(String(100), nullable=False)
    sensor_type = Column(String(50), nullable=False)
    unit = Column(String(20))
    min_threshold = Column(Float)
    max_threshold = Column(Float)
    reading_interval = Column(Integer, default=5)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    machine = relationship("Machine", back_populates="sensors")
    alerts = relationship("Alert", back_populates="sensor")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    reading_id = Column(BigInteger, primary_key=True, autoincrement=True)
    sensor_id = Column(Integer, nullable=False)
    machine_id = Column(Integer, nullable=False)
    line_id = Column(Integer, nullable=False)
    sensor_type = Column(String(50), nullable=False)
    value = Column(Float, nullable=False)
    quality = Column(Integer, default=100)
    device_id = Column(String(50))
    reading_time = Column(DateTime(timezone=True), server_default=func.now())
    batch_id = Column(String(50))

    __table_args__ = (
        Index("ix_readings_sensor", "sensor_id", "reading_time"),
        Index("ix_readings_machine", "machine_id", "reading_time"),
        Index("ix_readings_type", "sensor_type", "reading_time"),
    )


class Alert(Base):
    __tablename__ = "alerts"
    alert_id = Column(BigInteger, primary_key=True, autoincrement=True)
    sensor_id = Column(Integer, ForeignKey("sensors.sensor_id"), nullable=False)
    machine_id = Column(Integer, nullable=False)
    alert_type = Column(String(20), nullable=False)
    alert_message = Column(String(500))
    trigger_value = Column(Float)
    threshold_value = Column(Float)
    is_acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(String(100))
    acknowledged_at = Column(DateTime(timezone=True))
    triggered_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
    sensor = relationship("Sensor", back_populates="alerts")
    __table_args__ = (Index("ix_alerts_active", "is_acknowledged", "triggered_at"),)


class BatchTransfer(Base):
    __tablename__ = "batch_transfers"
    batch_id = Column(String(50), primary_key=True)
    source_device = Column(String(50))
    record_count = Column(Integer)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    transferred_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), default="pending")
    error_message = Column(Text)