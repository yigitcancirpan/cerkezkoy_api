from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Optional
from enum import Enum


class SensorType(str, Enum):
    SICAKLIK = "sicaklik"
    BASINC = "basinc"
    TITRESIM = "titresim"
    NEM = "nem"
    AKIM = "akim"
    GERILIM = "gerilim"
    HIZ = "hiz"
    DEBI = "debi"

class MachineStatus(str, Enum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"

class AlertType(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"
    INFO = "info"


class ProductionLineCreate(BaseModel):
    line_name: str
    location: str
    description: Optional[str] = None

class ProductionLineResponse(ProductionLineCreate):
    line_id: int
    is_active: bool
    class Config:
        from_attributes = True


class MachineCreate(BaseModel):
    line_id: int
    machine_name: str
    machine_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    install_date: Optional[date] = None

class MachineResponse(MachineCreate):
    machine_id: int
    status: str
    class Config:
        from_attributes = True


class SensorCreate(BaseModel):
    machine_id: int
    sensor_name: str
    sensor_type: SensorType
    unit: Optional[str] = None
    min_threshold: Optional[float] = None
    max_threshold: Optional[float] = None
    reading_interval: int = 5

class SensorResponse(SensorCreate):
    sensor_id: int
    is_active: bool
    class Config:
        from_attributes = True


class SensorReadingCreate(BaseModel):
    sensor_id: int
    machine_id: int
    line_id: int
    sensor_type: SensorType
    value: float
    quality: int = Field(default=100, ge=0, le=100)
    device_id: Optional[str] = None

class SensorReadingResponse(BaseModel):
    reading_id: int
    sensor_id: int
    value: float
    reading_time: datetime
    class Config:
        from_attributes = True

class SensorReadingBulk(BaseModel):
    readings: list[SensorReadingCreate]


class SensorStatsResponse(BaseModel):
    sensor_id: int
    sensor_type: str
    location: str
    machine_name: str
    avg_value: float
    max_value: float
    min_value: float
    stddev_value: Optional[float] = None
    reading_count: int
    period: str


class AlertCreate(BaseModel):
    sensor_id: int
    machine_id: int
    alert_type: AlertType
    alert_message: Optional[str] = None
    trigger_value: float
    threshold_value: float

class AlertResponse(AlertCreate):
    alert_id: int
    is_acknowledged: bool
    triggered_at: datetime
    resolved_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class AlertAcknowledge(BaseModel):
    acknowledged_by: str


class BatchTransferResponse(BaseModel):
    batch_id: str
    record_count: int
    start_time: datetime
    end_time: datetime
    transferred_at: datetime
    status: str
    class Config:
        from_attributes = True