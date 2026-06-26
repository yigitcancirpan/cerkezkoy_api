# Fabrika IoT Mimari Rehberi v3
## MSSQL 2022 Uyumlu — PostgreSQL Köprüsü ile Sorunsuz Geçiş
### Raspberry Pi OS Bookworm (Debian 12) için Güncellenmiş

---

## 1. Revize Mimari

```
┌──────────────────┐        MQTT         ┌─────────────────────────────────────┐
│      Pi 4         │  ────────────────►  │            Pi 3B                    │
│                   │  publish/subscribe  │                                     │
│  • Sensör okuma   │                    │  • Mosquitto Broker                 │
│  • paho-mqtt      │                    │  • MQTT Subscriber                  │
│  • SQLite cache   │                    │  • PostgreSQL (MSSQL şeması ile)    │
│  (haftalık sil)   │                    │  • FastAPI + SQLAlchemy             │
│                   │                    │  • Grafana                          │
└──────────────────┘                    └──────────────┬──────────────────────┘
                                                       │
                                          Günlük/Haftalık Batch Aktarım
                                                       │
                                                       ▼
                                        ┌──────────────────────────┐
                                        │   Fabrika Ağı Sunucusu   │
                                        │   MSSQL 2022             │
                                        │   (Aynı şema, kopyala-  │
                                        │    yapıştır geçiş)       │
                                        └──────────────────────────┘
```

### Geçiş Stratejisi

```
AŞAMA 1 (Şimdi):
  Pi 3B + PostgreSQL → Tablo yapısı MSSQL ile birebir aynı
  SQLAlchemy ORM → Veritabanı bağımsız kod
  FastAPI + Grafana → Aynı kalacak

AŞAMA 2 (Fabrika ağına geçiş):
  config.py'de connection string değiştir → Bitti
  Batch script ile PostgreSQL → MSSQL veri aktar
  Grafana data source'u MSSQL'e çevir → Bitti

AŞAMA 3 (Tam entegrasyon):
  Pi 3B sadece MQTT broker + edge cache olarak kalır
  FastAPI doğrudan MSSQL'e yazar
  Grafana doğrudan MSSQL'den okur
```

---

## 2. Veritabanı Şema Tasarımı (MSSQL 2022 Uyumlu)

### 2.1 MSSQL 2022 Şeması (Hedef — Fabrika Sunucusu)

```sql
-- ============================================
-- MSSQL 2022 — Fabrika IoT Veritabanı
-- ============================================

CREATE DATABASE FabrikaIoT;
GO
USE FabrikaIoT;
GO

-- ─── Üretim Hatları ───
CREATE TABLE ProductionLines (
    LineID          INT IDENTITY(1,1) PRIMARY KEY,
    LineName        NVARCHAR(100) NOT NULL,
    Location        NVARCHAR(50) NOT NULL,
    Description     NVARCHAR(500),
    IsActive        BIT DEFAULT 1,
    CreatedAt       DATETIME2 DEFAULT GETUTCDATE(),
    UpdatedAt       DATETIME2 DEFAULT GETUTCDATE()
);

-- ─── Makineler ───
CREATE TABLE Machines (
    MachineID       INT IDENTITY(1,1) PRIMARY KEY,
    LineID          INT NOT NULL FOREIGN KEY REFERENCES ProductionLines(LineID),
    MachineName     NVARCHAR(100) NOT NULL,
    MachineType     NVARCHAR(50),
    Manufacturer    NVARCHAR(100),
    Model           NVARCHAR(100),
    SerialNumber    NVARCHAR(100),
    InstallDate     DATE,
    Status          NVARCHAR(20) DEFAULT 'active',
    CreatedAt       DATETIME2 DEFAULT GETUTCDATE(),
    UpdatedAt       DATETIME2 DEFAULT GETUTCDATE()
);

CREATE INDEX IX_Machines_LineID ON Machines(LineID);
CREATE INDEX IX_Machines_Status ON Machines(Status);

-- ─── Sensör Tanımları ───
CREATE TABLE Sensors (
    SensorID        INT IDENTITY(1,1) PRIMARY KEY,
    MachineID       INT NOT NULL FOREIGN KEY REFERENCES Machines(MachineID),
    SensorName      NVARCHAR(100) NOT NULL,
    SensorType      NVARCHAR(50) NOT NULL,
    Unit            NVARCHAR(20),
    MinThreshold    FLOAT,
    MaxThreshold    FLOAT,
    ReadingInterval INT DEFAULT 5,
    IsActive        BIT DEFAULT 1,
    CreatedAt       DATETIME2 DEFAULT GETUTCDATE()
);

CREATE INDEX IX_Sensors_MachineID ON Sensors(MachineID);
CREATE INDEX IX_Sensors_Type ON Sensors(SensorType);

-- ─── Sensör Okumaları ───
CREATE PARTITION FUNCTION PF_SensorDate (DATETIME2)
AS RANGE RIGHT FOR VALUES (
    '2025-01-01', '2025-02-01', '2025-03-01', '2025-04-01',
    '2025-05-01', '2025-06-01', '2025-07-01', '2025-08-01',
    '2025-09-01', '2025-10-01', '2025-11-01', '2025-12-01',
    '2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01',
    '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01',
    '2026-09-01', '2026-10-01', '2026-11-01', '2026-12-01'
);

CREATE PARTITION SCHEME PS_SensorDate
AS PARTITION PF_SensorDate ALL TO ([PRIMARY]);

CREATE TABLE SensorReadings (
    ReadingID       BIGINT IDENTITY(1,1),
    SensorID        INT NOT NULL,
    MachineID       INT NOT NULL,
    LineID          INT NOT NULL,
    SensorType      NVARCHAR(50) NOT NULL,
    Value           FLOAT NOT NULL,
    Quality         INT DEFAULT 100,
    DeviceID        NVARCHAR(50),
    ReadingTime     DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    BatchID         NVARCHAR(50),
    CONSTRAINT PK_SensorReadings PRIMARY KEY (ReadingTime, ReadingID)
) ON PS_SensorDate(ReadingTime);

CREATE INDEX IX_SensorReadings_Sensor ON SensorReadings(SensorID, ReadingTime DESC);
CREATE INDEX IX_SensorReadings_Machine ON SensorReadings(MachineID, ReadingTime DESC);
CREATE INDEX IX_SensorReadings_Type ON SensorReadings(SensorType, ReadingTime DESC);
CREATE INDEX IX_SensorReadings_BatchID ON SensorReadings(BatchID) WHERE BatchID IS NOT NULL;

-- ─── Saatlik Özet ───
CREATE TABLE SensorHourlySummary (
    SummaryID       BIGINT IDENTITY(1,1) PRIMARY KEY,
    SensorID        INT NOT NULL,
    MachineID       INT NOT NULL,
    LineID          INT NOT NULL,
    SensorType      NVARCHAR(50) NOT NULL,
    HourBucket      DATETIME2 NOT NULL,
    AvgValue        FLOAT,
    MaxValue        FLOAT,
    MinValue        FLOAT,
    StdDevValue     FLOAT,
    ReadingCount    INT,
    CreatedAt       DATETIME2 DEFAULT GETUTCDATE()
);

CREATE UNIQUE INDEX IX_HourlySummary_Unique
ON SensorHourlySummary(SensorID, HourBucket);

-- ─── Alarmlar ───
CREATE TABLE Alerts (
    AlertID         BIGINT IDENTITY(1,1) PRIMARY KEY,
    SensorID        INT NOT NULL FOREIGN KEY REFERENCES Sensors(SensorID),
    MachineID       INT NOT NULL,
    AlertType       NVARCHAR(20) NOT NULL,
    AlertMessage    NVARCHAR(500),
    TriggerValue    FLOAT,
    ThresholdValue  FLOAT,
    IsAcknowledged  BIT DEFAULT 0,
    AcknowledgedBy  NVARCHAR(100),
    AcknowledgedAt  DATETIME2,
    TriggeredAt     DATETIME2 DEFAULT GETUTCDATE(),
    ResolvedAt      DATETIME2
);

CREATE INDEX IX_Alerts_Active ON Alerts(IsAcknowledged, TriggeredAt DESC);

-- ─── Batch Aktarım Kayıtları ───
CREATE TABLE BatchTransfers (
    BatchID         NVARCHAR(50) PRIMARY KEY,
    SourceDevice    NVARCHAR(50),
    RecordCount     INT,
    StartTime       DATETIME2,
    EndTime         DATETIME2,
    TransferredAt   DATETIME2 DEFAULT GETUTCDATE(),
    Status          NVARCHAR(20) DEFAULT 'pending',
    ErrorMessage    NVARCHAR(MAX)
);

-- ─── Saatlik Özet SP ───
CREATE PROCEDURE sp_GenerateHourlySummary
    @StartTime DATETIME2 = NULL,
    @EndTime DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;
    IF @StartTime IS NULL SET @StartTime = DATEADD(HOUR, -2, GETUTCDATE());
    IF @EndTime IS NULL SET @EndTime = GETUTCDATE();

    MERGE SensorHourlySummary AS target
    USING (
        SELECT SensorID, MachineID, LineID, SensorType,
               DATEADD(HOUR, DATEDIFF(HOUR, 0, ReadingTime), 0) AS HourBucket,
               AVG(Value) AS AvgValue, MAX(Value) AS MaxValue,
               MIN(Value) AS MinValue, STDEV(Value) AS StdDevValue, COUNT(*) AS ReadingCount
        FROM SensorReadings
        WHERE ReadingTime >= @StartTime AND ReadingTime < @EndTime
        GROUP BY SensorID, MachineID, LineID, SensorType,
                 DATEADD(HOUR, DATEDIFF(HOUR, 0, ReadingTime), 0)
    ) AS source
    ON target.SensorID = source.SensorID AND target.HourBucket = source.HourBucket
    WHEN MATCHED THEN
        UPDATE SET AvgValue=source.AvgValue, MaxValue=source.MaxValue,
                   MinValue=source.MinValue, StdDevValue=source.StdDevValue,
                   ReadingCount=source.ReadingCount
    WHEN NOT MATCHED THEN
        INSERT (SensorID,MachineID,LineID,SensorType,HourBucket,AvgValue,MaxValue,MinValue,StdDevValue,ReadingCount)
        VALUES (source.SensorID,source.MachineID,source.LineID,source.SensorType,source.HourBucket,
                source.AvgValue,source.MaxValue,source.MinValue,source.StdDevValue,source.ReadingCount);
END;
GO
```

### 2.2 PostgreSQL Şeması (Pi 3B)

```sql
-- Önce dosyayı /tmp'ye kopyala, sonra çalıştır:
-- cp /home/server/cerkezkoy_api/schema.sql /tmp/schema.sql
-- sudo -u postgres psql -d cerkezkoy_db -f /tmp/schema.sql

CREATE TABLE production_lines (
    line_id         SERIAL PRIMARY KEY,
    line_name       VARCHAR(100) NOT NULL,
    location        VARCHAR(50) NOT NULL,
    description     VARCHAR(500),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE machines (
    machine_id      SERIAL PRIMARY KEY,
    line_id         INT NOT NULL REFERENCES production_lines(line_id),
    machine_name    VARCHAR(100) NOT NULL,
    machine_type    VARCHAR(50),
    manufacturer    VARCHAR(100),
    model           VARCHAR(100),
    serial_number   VARCHAR(100),
    install_date    DATE,
    status          VARCHAR(20) DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ix_machines_line ON machines(line_id);
CREATE INDEX ix_machines_status ON machines(status);

CREATE TABLE sensors (
    sensor_id       SERIAL PRIMARY KEY,
    machine_id      INT NOT NULL REFERENCES machines(machine_id),
    sensor_name     VARCHAR(100) NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    unit            VARCHAR(20),
    min_threshold   FLOAT,
    max_threshold   FLOAT,
    reading_interval INT DEFAULT 5,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ix_sensors_machine ON sensors(machine_id);
CREATE INDEX ix_sensors_type ON sensors(sensor_type);

CREATE TABLE sensor_readings (
    reading_id      BIGSERIAL,
    sensor_id       INT NOT NULL,
    machine_id      INT NOT NULL,
    line_id         INT NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    quality         INT DEFAULT 100,
    device_id       VARCHAR(50),
    reading_time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id        VARCHAR(50),
    PRIMARY KEY (reading_time, reading_id)
) PARTITION BY RANGE (reading_time);

-- Aylık partition'lar (gerektiğinde yeni ay ekle)
CREATE TABLE sensor_readings_2026_03 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE sensor_readings_2026_04 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE sensor_readings_2026_05 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE sensor_readings_2026_06 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX ix_readings_sensor ON sensor_readings(sensor_id, reading_time DESC);
CREATE INDEX ix_readings_machine ON sensor_readings(machine_id, reading_time DESC);
CREATE INDEX ix_readings_type ON sensor_readings(sensor_type, reading_time DESC);

CREATE TABLE sensor_hourly_summary (
    summary_id      BIGSERIAL PRIMARY KEY,
    sensor_id       INT NOT NULL,
    machine_id      INT NOT NULL,
    line_id         INT NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    hour_bucket     TIMESTAMPTZ NOT NULL,
    avg_value       FLOAT,
    max_value       FLOAT,
    min_value       FLOAT,
    stddev_value    FLOAT,
    reading_count   INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX ix_hourly_unique ON sensor_hourly_summary(sensor_id, hour_bucket);

CREATE TABLE alerts (
    alert_id        BIGSERIAL PRIMARY KEY,
    sensor_id       INT NOT NULL REFERENCES sensors(sensor_id),
    machine_id      INT NOT NULL,
    alert_type      VARCHAR(20) NOT NULL,
    alert_message   VARCHAR(500),
    trigger_value   FLOAT,
    threshold_value FLOAT,
    is_acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,
    triggered_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX ix_alerts_active ON alerts(is_acknowledged, triggered_at DESC);

CREATE TABLE batch_transfers (
    batch_id        VARCHAR(50) PRIMARY KEY,
    source_device   VARCHAR(50),
    record_count    INT,
    start_time      TIMESTAMPTZ,
    end_time        TIMESTAMPTZ,
    transferred_at  TIMESTAMPTZ DEFAULT NOW(),
    status          VARCHAR(20) DEFAULT 'pending',
    error_message   TEXT
);

CREATE OR REPLACE FUNCTION generate_hourly_summary(
    p_start_time TIMESTAMPTZ DEFAULT NULL,
    p_end_time TIMESTAMPTZ DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    IF p_start_time IS NULL THEN p_start_time := NOW() - INTERVAL '2 hours'; END IF;
    IF p_end_time IS NULL THEN p_end_time := NOW(); END IF;

    INSERT INTO sensor_hourly_summary
        (sensor_id, machine_id, line_id, sensor_type, hour_bucket,
         avg_value, max_value, min_value, stddev_value, reading_count)
    SELECT sensor_id, machine_id, line_id, sensor_type,
           date_trunc('hour', reading_time) AS hour_bucket,
           AVG(value), MAX(value), MIN(value), STDDEV(value), COUNT(*)
    FROM sensor_readings
    WHERE reading_time >= p_start_time AND reading_time < p_end_time
    GROUP BY sensor_id, machine_id, line_id, sensor_type, date_trunc('hour', reading_time)
    ON CONFLICT (sensor_id, hour_bucket)
    DO UPDATE SET avg_value=EXCLUDED.avg_value, max_value=EXCLUDED.max_value,
                  min_value=EXCLUDED.min_value, stddev_value=EXCLUDED.stddev_value,
                  reading_count=EXCLUDED.reading_count;
END;
$$ LANGUAGE plpgsql;
```

### 2.3 Fark Tablosu

```
┌────────────────────┬──────────────────────┬──────────────────────┐
│ Kavram             │ PostgreSQL (Pi 3B)   │ MSSQL 2022 (Fabrika) │
├────────────────────┼──────────────────────┼──────────────────────┤
│ Otomatik ID        │ SERIAL / BIGSERIAL   │ INT IDENTITY(1,1)    │
│ Boolean            │ BOOLEAN              │ BIT                  │
│ Zaman damgası      │ TIMESTAMPTZ          │ DATETIME2            │
│ Şimdiki zaman      │ NOW()                │ GETUTCDATE()         │
│ String             │ VARCHAR              │ NVARCHAR             │
│ Büyük metin        │ TEXT                 │ NVARCHAR(MAX)        │
│ Partition           │ PARTITION BY RANGE   │ Partition Function   │
│ Upsert             │ ON CONFLICT DO UPDATE│ MERGE                │
│ Tablo isimleri     │ snake_case           │ PascalCase           │
│ Bağlantı portu     │ 5432                 │ 1433                 │
│ Driver             │ psycopg2             │ pyodbc / pymssql     │
└────────────────────┴──────────────────────┴──────────────────────┘

SQLAlchemy kullandığımızda bu farkların %90'ını ORM halleder!
Sadece connection string değişir.
```

---

## 3. SQLAlchemy ORM Modelleri

### 3.1 models/database.py

```python
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import settings

DATABASE_URL = (
    f"postgresql://{settings.pg_user}:{settings.pg_password}"
    f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
)

# MSSQL geçişinde sadece bu satırı aktif et:
# DATABASE_URL = (
#     f"mssql+pyodbc://{settings.mssql_user}:{settings.mssql_password}"
#     f"@{settings.mssql_host}:{settings.mssql_port}/{settings.mssql_db}"
#     f"?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
# )

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 3.2 models/orm_models.py

```python
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
```

### 3.3 models/schemas.py

```python
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
```

---

## 4. config.py

```python
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "fastapi-server"
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "fabrika_iot"
    pg_user: str = "fabrika_user"
    pg_password: str = "güçlü_şifre"

    mssql_host: str = "192.168.1.200"
    mssql_port: int = 1433
    mssql_db: str = "FabrikaIoT"
    mssql_user: str = "iot_user"
    mssql_password: str = ""

    active_db: str = "postgresql"

    api_title: str = "Fabrika IoT API"
    api_version: str = "1.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
```

---

## 5. FastAPI Uygulama

### 5.1 main.py

```python
#!/usr/bin/env python3
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models.database import engine, Base
from services.mqtt_service import mqtt_service
from routers import sensors, machines, alerts, batch_transfer, websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Tablolar oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    print("MQTT bağlanıyor...")
    mqtt_service.connect()
    yield
    mqtt_service.disconnect()

app = FastAPI(title=settings.api_title, version=settings.api_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(sensors.router)
app.include_router(machines.router)
app.include_router(alerts.router)
app.include_router(batch_transfer.router)
app.include_router(websocket_router.router)


@app.get("/", tags=["Sistem"])
async def root():
    return {"service": settings.api_title, "version": settings.api_version,
            "active_db": settings.active_db, "docs": "/docs"}

@app.get("/health", tags=["Sistem"])
async def health():
    return {"status": "healthy", "database": settings.active_db,
            "mqtt_topics": len(mqtt_service._latest_data)}
```

### 5.2 routers/sensors.py

```python
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional

from models.database import get_db
from models.orm_models import SensorReading, Sensor, Machine, ProductionLine
from models.schemas import (
    SensorReadingCreate, SensorReadingBulk, SensorReadingResponse,
    SensorStatsResponse, SensorType
)

router = APIRouter(prefix="/api/v1/sensors", tags=["Sensörler"])


def parse_time_range(time_range: str) -> datetime:
    mapping = {"1h": timedelta(hours=1), "6h": timedelta(hours=6),
               "12h": timedelta(hours=12), "24h": timedelta(hours=24),
               "7d": timedelta(days=7), "30d": timedelta(days=30)}
    delta = mapping.get(time_range)
    if not delta:
        raise HTTPException(400, f"Geçersiz zaman aralığı: {time_range}")
    return datetime.utcnow() - delta


@router.get("/latest")
def get_latest_readings(location: Optional[str] = None,
                        sensor_type: Optional[str] = None,
                        db: Session = Depends(get_db)):
    subquery = (
        db.query(SensorReading.sensor_id,
                 func.max(SensorReading.reading_time).label("max_time"))
        .filter(SensorReading.reading_time > datetime.utcnow() - timedelta(minutes=5))
    )
    if location:
        subquery = subquery.join(ProductionLine, SensorReading.line_id == ProductionLine.line_id
                                 ).filter(ProductionLine.location == location)
    if sensor_type:
        subquery = subquery.filter(SensorReading.sensor_type == sensor_type)
    subquery = subquery.group_by(SensorReading.sensor_id).subquery()

    readings = (db.query(SensorReading)
                .join(subquery, ((SensorReading.sensor_id == subquery.c.sensor_id) &
                                 (SensorReading.reading_time == subquery.c.max_time)))
                .all())

    return {"count": len(readings), "data": [
        {"sensor_id": r.sensor_id, "machine_id": r.machine_id,
         "sensor_type": r.sensor_type, "value": r.value,
         "quality": r.quality, "reading_time": r.reading_time.isoformat()}
        for r in readings
    ]}


@router.get("/history/{sensor_id}")
def get_sensor_history(sensor_id: int,
                       time_range: str = Query("1h", description="1h, 6h, 24h, 7d, 30d"),
                       db: Session = Depends(get_db)):
    since = parse_time_range(time_range)
    readings = (db.query(SensorReading.value, SensorReading.quality, SensorReading.reading_time)
                .filter(SensorReading.sensor_id == sensor_id, SensorReading.reading_time >= since)
                .order_by(desc(SensorReading.reading_time)).limit(5000).all())
    return {"sensor_id": sensor_id, "time_range": time_range, "count": len(readings),
            "data": [{"value": r.value, "quality": r.quality, "time": r.reading_time.isoformat()}
                     for r in readings]}


@router.get("/stats/{sensor_id}")
def get_sensor_stats(sensor_id: int, time_range: str = Query("24h"),
                     db: Session = Depends(get_db)):
    since = parse_time_range(time_range)
    stats = (db.query(func.avg(SensorReading.value).label("avg_value"),
                      func.max(SensorReading.value).label("max_value"),
                      func.min(SensorReading.value).label("min_value"),
                      func.stddev(SensorReading.value).label("stddev_value"),
                      func.count(SensorReading.reading_id).label("reading_count"))
             .filter(SensorReading.sensor_id == sensor_id, SensorReading.reading_time >= since)
             .first())
    sensor = db.query(Sensor).filter(Sensor.sensor_id == sensor_id).first()
    if not sensor:
        raise HTTPException(404, "Sensör bulunamadı")
    machine = db.query(Machine).filter(Machine.machine_id == sensor.machine_id).first()
    line = db.query(ProductionLine).filter(ProductionLine.line_id == machine.line_id).first()
    return SensorStatsResponse(
        sensor_id=sensor_id, sensor_type=sensor.sensor_type,
        location=line.location if line else "",
        machine_name=machine.machine_name if machine else "",
        avg_value=round(stats.avg_value or 0, 2), max_value=round(stats.max_value or 0, 2),
        min_value=round(stats.min_value or 0, 2),
        stddev_value=round(stats.stddev_value or 0, 3) if stats.stddev_value else None,
        reading_count=stats.reading_count or 0, period=time_range)


@router.post("/reading")
def post_reading(reading: SensorReadingCreate, db: Session = Depends(get_db)):
    db_reading = SensorReading(
        sensor_id=reading.sensor_id, machine_id=reading.machine_id,
        line_id=reading.line_id, sensor_type=reading.sensor_type.value,
        value=reading.value, quality=reading.quality, device_id=reading.device_id)
    db.add(db_reading)
    db.commit()
    return {"status": "ok", "reading_id": db_reading.reading_id}


@router.post("/readings/bulk")
def post_bulk_readings(bulk: SensorReadingBulk, db: Session = Depends(get_db)):
    db_readings = [
        SensorReading(sensor_id=r.sensor_id, machine_id=r.machine_id, line_id=r.line_id,
                      sensor_type=r.sensor_type.value, value=r.value,
                      quality=r.quality, device_id=r.device_id)
        for r in bulk.readings
    ]
    db.add_all(db_readings)
    db.commit()
    return {"status": "ok", "count": len(db_readings)}
```

### 5.3 routers/machines.py

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from models.database import get_db
from models.orm_models import Machine, ProductionLine, Sensor
from models.schemas import (
    MachineCreate, MachineResponse,
    ProductionLineCreate, ProductionLineResponse,
    SensorCreate, SensorResponse, MachineStatus,
)

router = APIRouter(prefix="/api/v1/machines", tags=["Makineler"])


# ── Üretim Hatları ──────────────────────────────────────────────

@router.get("/lines", response_model=list[ProductionLineResponse])
def list_production_lines(db: Session = Depends(get_db)):
    return db.query(ProductionLine).order_by(ProductionLine.line_id).all()


@router.post("/lines", response_model=ProductionLineResponse, status_code=201)
def create_production_line(line: ProductionLineCreate, db: Session = Depends(get_db)):
    db_line = ProductionLine(**line.model_dump())
    db.add(db_line)
    db.commit()
    db.refresh(db_line)
    return db_line


# ── Makineler ────────────────────────────────────────────────────

@router.get("/", response_model=list[MachineResponse])
def list_machines(line_id: Optional[int] = None,
                  status: Optional[MachineStatus] = None,
                  db: Session = Depends(get_db)):
    q = db.query(Machine)
    if line_id:
        q = q.filter(Machine.line_id == line_id)
    if status:
        q = q.filter(Machine.status == status.value)
    return q.order_by(Machine.machine_id).all()


@router.get("/{machine_id}", response_model=MachineResponse)
def get_machine(machine_id: int, db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(404, "Makine bulunamadı")
    return machine


@router.post("/", response_model=MachineResponse, status_code=201)
def create_machine(machine: MachineCreate, db: Session = Depends(get_db)):
    line = db.query(ProductionLine).filter(
        ProductionLine.line_id == machine.line_id
    ).first()
    if not line:
        raise HTTPException(404, "Üretim hattı bulunamadı")
    db_machine = Machine(**machine.model_dump())
    db.add(db_machine)
    db.commit()
    db.refresh(db_machine)
    return db_machine


@router.patch("/{machine_id}/status")
def update_machine_status(machine_id: int, status: MachineStatus,
                          db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(404, "Makine bulunamadı")
    machine.status = status.value
    db.commit()
    return {"status": "ok", "machine_id": machine_id, "new_status": status.value}


# ── Sensör Tanımlama ────────────────────────────────────────────

@router.get("/{machine_id}/sensors", response_model=list[SensorResponse])
def list_sensors_for_machine(machine_id: int, db: Session = Depends(get_db)):
    return (db.query(Sensor)
            .filter(Sensor.machine_id == machine_id)
            .order_by(Sensor.sensor_id).all())


@router.post("/{machine_id}/sensors", response_model=SensorResponse, status_code=201)
def create_sensor(machine_id: int, sensor: SensorCreate,
                  db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.machine_id == machine_id).first()
    if not machine:
        raise HTTPException(404, "Makine bulunamadı")
    db_sensor = Sensor(machine_id=machine_id, **sensor.model_dump(exclude={"machine_id"}))
    db.add(db_sensor)
    db.commit()
    db.refresh(db_sensor)
    return db_sensor
```

### 5.4 routers/alerts.py

```python
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta
from typing import Optional

from models.database import get_db
from models.orm_models import Alert, Sensor
from models.schemas import AlertCreate, AlertResponse, AlertAcknowledge, AlertType

router = APIRouter(prefix="/api/v1/alerts", tags=["Alarmlar"])


@router.get("/", response_model=list[AlertResponse])
def list_alerts(is_acknowledged: Optional[bool] = None,
                alert_type: Optional[AlertType] = None,
                machine_id: Optional[int] = None,
                limit: int = Query(50, ge=1, le=500),
                db: Session = Depends(get_db)):
    q = db.query(Alert)
    if is_acknowledged is not None:
        q = q.filter(Alert.is_acknowledged == is_acknowledged)
    if alert_type:
        q = q.filter(Alert.alert_type == alert_type.value)
    if machine_id:
        q = q.filter(Alert.machine_id == machine_id)
    return q.order_by(desc(Alert.triggered_at)).limit(limit).all()


@router.get("/active", response_model=list[AlertResponse])
def get_active_alerts(db: Session = Depends(get_db)):
    return (db.query(Alert)
            .filter(Alert.is_acknowledged == False, Alert.resolved_at == None)
            .order_by(desc(Alert.triggered_at)).all())


@router.get("/summary")
def alert_summary(hours: int = Query(24, ge=1, le=720),
                  db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=hours)
    alerts = db.query(Alert).filter(Alert.triggered_at >= since).all()
    total = len(alerts)
    by_type = {}
    for a in alerts:
        by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
    unacked = sum(1 for a in alerts if not a.is_acknowledged)
    return {"period_hours": hours, "total": total, "unacknowledged": unacked,
            "by_type": by_type}


@router.post("/", response_model=AlertResponse, status_code=201)
def create_alert(alert: AlertCreate, db: Session = Depends(get_db)):
    sensor = db.query(Sensor).filter(Sensor.sensor_id == alert.sensor_id).first()
    if not sensor:
        raise HTTPException(404, "Sensör bulunamadı")
    db_alert = Alert(**alert.model_dump())
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert


@router.patch("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, body: AlertAcknowledge,
                      db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alarm bulunamadı")
    alert.is_acknowledged = True
    alert.acknowledged_by = body.acknowledged_by
    alert.acknowledged_at = datetime.utcnow()
    db.commit()
    return {"status": "ok", "alert_id": alert_id, "acknowledged_by": body.acknowledged_by}


@router.patch("/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alarm bulunamadı")
    alert.resolved_at = datetime.utcnow()
    if not alert.is_acknowledged:
        alert.is_acknowledged = True
        alert.acknowledged_at = datetime.utcnow()
        alert.acknowledged_by = "auto-resolve"
    db.commit()
    return {"status": "ok", "alert_id": alert_id, "resolved_at": alert.resolved_at.isoformat()}
```

### 5.5 routers/batch_transfer.py

```python
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta

from models.database import get_db
from models.orm_models import SensorReading, BatchTransfer

router = APIRouter(prefix="/api/v1/batch", tags=["Toplu Transfer"])


@router.get("/transfers", response_model=list)
def list_transfers(limit: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db)):
    transfers = (db.query(BatchTransfer)
                 .order_by(desc(BatchTransfer.transferred_at))
                 .limit(limit).all())
    return [
        {"batch_id": t.batch_id, "record_count": t.record_count,
         "start_time": t.start_time.isoformat() if t.start_time else None,
         "end_time": t.end_time.isoformat() if t.end_time else None,
         "transferred_at": t.transferred_at.isoformat() if t.transferred_at else None,
         "status": t.status, "error_message": t.error_message}
        for t in transfers
    ]


@router.post("/prepare")
def prepare_batch(hours: int = Query(24, ge=1, le=168),
                  db: Session = Depends(get_db)):
    """PostgreSQL'den MSSQL'e aktarılacak verileri hazırla."""
    since = datetime.utcnow() - timedelta(hours=hours)

    # Daha önce transfer edilmemiş okumalar
    already_transferred = (
        db.query(SensorReading.reading_id)
        .filter(SensorReading.batch_id != None)
        .subquery()
    )
    readings = (
        db.query(SensorReading)
        .filter(SensorReading.reading_time >= since,
                ~SensorReading.reading_id.in_(already_transferred))
        .order_by(SensorReading.reading_time)
        .all()
    )

    if not readings:
        return {"status": "empty", "message": "Aktarılacak yeni okuma yok"}

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    for r in readings:
        r.batch_id = batch_id
    db.commit()

    return {
        "status": "prepared",
        "batch_id": batch_id,
        "record_count": len(readings),
        "time_range": {
            "start": readings[0].reading_time.isoformat(),
            "end": readings[-1].reading_time.isoformat(),
        },
    }


@router.post("/transfer/{batch_id}")
def execute_transfer(batch_id: str, db: Session = Depends(get_db)):
    """
    Hazırlanan batch'i MSSQL'e aktar.
    Not: MSSQL bağlantısı aktif olduğunda buraya eklenir.
    Şu an sadece batch kaydı oluşturur (placeholder).
    """
    readings = (db.query(SensorReading)
                .filter(SensorReading.batch_id == batch_id).all())
    if not readings:
        raise HTTPException(404, f"Batch bulunamadı: {batch_id}")

    transfer = BatchTransfer(
        batch_id=batch_id,
        source_device="pi3b-postgresql",
        record_count=len(readings),
        start_time=readings[0].reading_time,
        end_time=readings[-1].reading_time,
        status="completed",          # MSSQL entegrasyonunda "pending" olacak
    )
    db.add(transfer)
    db.commit()

    return {
        "status": "completed",
        "batch_id": batch_id,
        "record_count": len(readings),
        "message": "MSSQL bağlantısı aktif olduğunda gerçek transfer başlayacak",
    }
```

### 5.6 routers/websocket_router.py

```python
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.mqtt_service import mqtt_service

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """
    Canlı sensör verilerini WebSocket üzerinden yayınla.
    MQTT'den gelen her mesaj anında bağlı istemcilere iletilir.
    Grafana Live veya özel dashboard'lar için kullanılabilir.
    """
    await ws.accept()
    queue = mqtt_service.add_listener()
    try:
        while True:
            message = await queue.get()
            await ws.send_text(json.dumps(message))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket hatası: {e}")
    finally:
        mqtt_service.remove_listener(queue)


@router.websocket("/ws/subscribe/{topic_filter}")
async def websocket_filtered(ws: WebSocket, topic_filter: str):
    """
    Belirli bir topic filtresiyle canlı veri al.
    Örn: /ws/subscribe/fabrika%2Fhat1  → sadece fabrika/hat1/* mesajları
    topic_filter URL-encoded gelir, decode edilir.
    """
    await ws.accept()
    queue = mqtt_service.add_listener()
    decoded_filter = topic_filter.replace("%2F", "/")
    try:
        while True:
            message = await queue.get()
            if message.get("topic", "").startswith(decoded_filter):
                await ws.send_text(json.dumps(message))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket hatası: {e}")
    finally:
        mqtt_service.remove_listener(queue)


@router.get("/api/v1/live/snapshot", tags=["Canlı Veri"])
def live_snapshot():
    """MQTT'den en son alınan tüm verilerin anlık görüntüsü."""
    data = mqtt_service.get_latest()
    return {"topic_count": len(data), "data": data}
```

---

## 6. MQTT Service

### services/mqtt_service.py

```python
import json
import asyncio
from datetime import datetime
from paho.mqtt import client as mqtt_client
from sqlalchemy.orm import Session

from config import settings
from models.database import SessionLocal
from models.orm_models import SensorReading


class MQTTService:
    def __init__(self):
        self.client = mqtt_client.Client(
            client_id=settings.mqtt_client_id,
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._listeners: list[asyncio.Queue] = []
        self._latest_data: dict = {}

    def connect(self):
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.connect(settings.mqtt_broker, settings.mqtt_port)
        self.client.loop_start()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        print(f"✓ MQTT Broker bağlandı (rc={rc})")
        client.subscribe("fabrika/#", qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            self._latest_data[msg.topic] = {
                "payload": payload, "received_at": datetime.utcnow().isoformat()
            }
            if len(topic_parts) >= 3 and topic_parts[2] not in ("bulk", "durum"):
                self._write_to_db(topic_parts, payload)
            message = {"topic": msg.topic, "payload": payload,
                       "ts": datetime.utcnow().isoformat()}
            for queue in self._listeners:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass
        except Exception as e:
            print(f"MQTT mesaj hatası: {e}")

    def _write_to_db(self, topic_parts: list, payload: dict):
        db: Session = SessionLocal()
        try:
            reading = SensorReading(
                sensor_id=payload.get("sensor_id", 0),
                machine_id=payload.get("machine_id", 0),
                line_id=payload.get("line_id", 0),
                sensor_type=topic_parts[2],
                value=float(payload.get("value", 0)),
                quality=payload.get("quality", 100),
                device_id=payload.get("device", "unknown"),
            )
            db.add(reading)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"DB yazma hatası: {e}")
        finally:
            db.close()

    def publish(self, topic: str, payload: dict, qos: int = 1):
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def get_latest(self) -> dict:
        return self._latest_data

    def add_listener(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        self._listeners.append(queue)
        return queue

    def remove_listener(self, queue: asyncio.Queue):
        if queue in self._listeners:
            self._listeners.remove(queue)


mqtt_service = MQTTService()
```

---

## 7. Grafana — PostgreSQL ve MSSQL Data Source

### 7.1 PostgreSQL Data Source (Aşama 1)

```
Grafana → Connections → Data Sources → Add
  Type: PostgreSQL
  Host: localhost:5432
  Database: fabrika_iot
  User: fabrika_user
  SSL Mode: disable
  Version: 15
```

### 7.2 MSSQL Data Source (Aşama 2)

```
Grafana → Connections → Data Sources → Add
  Type: Microsoft SQL Server
  Host: 192.168.1.200:1433
  Database: FabrikaIoT
  Auth: SQL Server Authentication
  User: iot_user
  Encrypt: false
```

### 7.3 Grafana Sorguları

```sql
-- Sıcaklık Zaman Serisi (PostgreSQL):
SELECT
    date_trunc('minute', reading_time) AS time,
    sensor_type,
    AVG(value) AS value
FROM sensor_readings
WHERE reading_time >= $__timeFrom()
  AND reading_time <= $__timeTo()
  AND sensor_type = 'sicaklik'
GROUP BY 1, sensor_type
ORDER BY 1;

-- MSSQL versiyonu:
SELECT
    DATEADD(MINUTE, DATEDIFF(MINUTE, 0, ReadingTime), 0) AS time,
    SensorType,
    AVG(Value) AS value
FROM SensorReadings
WHERE ReadingTime >= $__timeFrom
  AND ReadingTime <= $__timeTo
  AND SensorType = 'sicaklik'
GROUP BY DATEADD(MINUTE, DATEDIFF(MINUTE, 0, ReadingTime), 0), SensorType
ORDER BY 1;
```

```sql
-- Alarm Sayıları (PostgreSQL):
SELECT alert_type, COUNT(*) AS count
FROM alerts
WHERE is_acknowledged = FALSE
  AND triggered_at > NOW() - INTERVAL '24 hours'
GROUP BY alert_type;
```

---

## 8. Pi 4 — Sensör Publisher

```python
#!/usr/bin/env python3
import json, time, sqlite3, random
from datetime import datetime
from paho.mqtt import client as mqtt_client

BROKER_IP = "192.168.1.100"   # Pi 3B IP
BROKER_PORT = 1883
CLIENT_ID = "pi4-hat1"

SENSOR_MAP = {
    "sicaklik": {"sensor_id": 1, "machine_id": 1, "line_id": 1, "unit": "°C"},
    "basinc":   {"sensor_id": 2, "machine_id": 1, "line_id": 1, "unit": "bar"},
    "titresim": {"sensor_id": 3, "machine_id": 1, "line_id": 1, "unit": "mm/s"},
    "nem":      {"sensor_id": 4, "machine_id": 1, "line_id": 1, "unit": "%"},
    "akim":     {"sensor_id": 5, "machine_id": 1, "line_id": 1, "unit": "A"},
}

cache_conn = sqlite3.connect("/home/pi/cache/sensor_cache.db")
cache_conn.execute("""
    CREATE TABLE IF NOT EXISTS cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT, payload TEXT, ts TEXT, synced INTEGER DEFAULT 0
    )
""")


def on_connect(client, userdata, flags, rc, properties=None):
    print("✓ MQTT bağlandı" if rc == 0 else f"✗ Bağlantı hatası: {rc}")


def read_sensors() -> dict:
    """Gerçek sensörlerini buraya bağla"""
    return {
        "sicaklik": round(random.uniform(20.0, 85.0), 2),
        "basinc":   round(random.uniform(1.0, 10.0), 2),
        "titresim": round(random.uniform(0.0, 5.0), 3),
        "nem":      round(random.uniform(30.0, 90.0), 1),
        "akim":     round(random.uniform(0.5, 15.0), 2),
    }


def main():
    client = mqtt_client.Client(
        client_id=CLIENT_ID,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
    )
    client.on_connect = on_connect
    client.will_set("fabrika/hat1/durum", json.dumps({"durum": "offline"}), qos=1, retain=True)
    client.connect(BROKER_IP, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            sensors = read_sensors()
            ts = datetime.utcnow().isoformat()
            for name, value in sensors.items():
                meta = SENSOR_MAP[name]
                topic = f"fabrika/hat1/{name}"
                payload = {
                    "sensor_id": meta["sensor_id"], "machine_id": meta["machine_id"],
                    "line_id": meta["line_id"], "value": value,
                    "unit": meta["unit"], "quality": 100, "device": CLIENT_ID, "ts": ts
                }
                client.publish(topic, json.dumps(payload), qos=1)
                cache_conn.execute(
                    "INSERT INTO cache (topic, payload, ts) VALUES (?,?,?)",
                    (topic, json.dumps(payload), ts))
            cache_conn.commit()
            cache_conn.execute("DELETE FROM cache WHERE ts < datetime('now', '-7 days')")
            cache_conn.commit()
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    finally:
        client.loop_stop()
        client.disconnect()
        cache_conn.close()


if __name__ == "__main__":
    main()
```

---

## 9. Kurulum — Pi 3B (Raspberry Pi OS Bookworm)

> **v3 Değişikliği:** `software-properties-common` Bookworm'da mevcut değil, Grafana kurulumu güncellendi. `dhcpcd` servisi yok, ağ ayarları `nmcli` ile yapılıyor.

```bash
#!/bin/bash
# pi3b_setup.sh — Bookworm (Debian 12) için güncel kurulum

echo "=== Pi 3B Fabrika IoT Kurulum v3 ==="

# 1. Sistem güncelle
sudo apt update && sudo apt upgrade -y

# 2. Mosquitto
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto

# 3. PostgreSQL
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql

# Veritabanı ve kullanıcı oluştur
sudo -u postgres psql -c "CREATE USER fabrika_user WITH PASSWORD 'güçlü_şifre';"
sudo -u postgres psql -c "CREATE DATABASE fabrika_iot OWNER fabrika_user;"

# ÖNEMLİ: Schema dosyasını /tmp'ye kopyala, sonra çalıştır
# cp /home/server/fabrika_api/schema.sql /tmp/schema.sql
# sudo -u postgres psql -d fabrika_iot -f /tmp/schema.sql
# rm /tmp/schema.sql

# 4. Grafana (Bookworm uyumlu kurulum)
sudo apt install -y apt-transport-https
sudo mkdir -p /etc/apt/keyrings
wget -q -O - https://apt.grafana.com/gpg.key | \
    sudo gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | \
    sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt update && sudo apt install -y grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server

# 5. Python bağımlılıkları
pip3 install --break-system-packages \
    fastapi uvicorn[standard] paho-mqtt sqlalchemy psycopg2-binary \
    pydantic pydantic-settings python-dotenv

# MSSQL geçişi için (ileride):
# sudo apt install -y unixodbc-dev
# pip3 install --break-system-packages pyodbc

# 6. Tüm servisleri başlat
sudo systemctl start mosquitto
sudo systemctl start postgresql
sudo systemctl start grafana-server

echo "=== Kurulum tamamlandı ==="
echo "Grafana: http://$(hostname -I | awk '{print $1}'):3000  (admin/admin)"
echo "API:     http://$(hostname -I | awk '{print $1}'):8000/docs"
```

---

## 10. Ağ Yapılandırması — Bookworm (nmcli)

> **v3 Değişikliği:** Bookworm'da `dhcpcd` servisi kaldırıldı, yerini `NetworkManager` aldı.

### WiFi ve Ethernet Aynı Anda Bağlıysa (Route Çakışması)

```bash
# Mevcut bağlantı isimlerini gör
nmcli connection show

# Ethernet'e yüksek metric ver (internet trafiği buradan çıkmasın)
sudo nmcli connection modify "Wired connection 1" ipv4.route-metric 300

# WiFi'ya düşük metric ver (internet bu üzerinden çıksın)
sudo nmcli connection modify "YourWifi" ipv4.route-metric 100

# Uygula
sudo nmcli connection up "Wired connection 1"
sudo nmcli connection up "YourWifi"

# Kontrol
ip route show
# "default via ... dev wlan0 metric 100" üstte olmalı
```

### Statik IP Atama (nmcli)

```bash
# Ethernet'e statik IP (PLC ağı için)
sudo nmcli connection modify "Wired connection 1" \
    ipv4.method manual \
    ipv4.addresses "192.168.1.50/24" \
    ipv4.gateway "" \
    ipv4.dns ""

# Uygula
sudo nmcli connection up "Wired connection 1"
```

---

## 11. SCP ile Dosya Gönderme

```bash
# Windows'tan Pi'ye tek dosya gönder
scp C:\Users\kullanici\Downloads\script.py server@192.168.x.x:/home/server/

# Klasöre göndermek için klasörü önce oluştur
ssh server@192.168.x.x "mkdir -p /home/server/proje_klasoru"
scp dosya.py server@192.168.x.x:/home/server/proje_klasoru/

# ÖNEMLİ: scp hedef klasörün önceden var olmasını bekler.
# Klasör yoksa "No such file or directory" hatası alırsın.
```

---

## 12. Geçiş Günü Checklist

```
MSSQL'e Geçiş (Aşama 2):

  1. MSSQL 2022'de şemayı çalıştır (Bölüm 2.1)
  2. MSSQL kullanıcısı oluştur, yetki ver
  3. Pi 3B'ye ODBC driver kur:
     sudo apt install -y unixodbc-dev
     pip3 install --break-system-packages pyodbc
  4. .env dosyasında ACTIVE_DB=mssql yap
  5. config.py'de MSSQL bilgilerini gir
  6. database.py'de MSSQL connection string'i aktif et
  7. FastAPI'yi yeniden başlat: sudo systemctl restart fabrika-api
  8. Batch transfer endpoint'i ile eski verileri aktar
  9. Grafana'da: Connections → Data Sources → Add → MSSQL
  10. Test et, doğrula
```

---

## 13. Proje Dosya Yapısı

```
fabrika_api/
├── main.py
├── config.py
├── .env
├── requirements.txt
├── schema_postgresql.sql
├── schema_mssql.sql
├── models/
│   ├── __init__.py
│   ├── database.py
│   ├── orm_models.py
│   └── schemas.py
├── routers/
│   ├── __init__.py
│   ├── sensors.py
│   ├── machines.py
│   ├── alerts.py
│   ├── batch_transfer.py
│   └── websocket_router.py
├── services/
│   ├── __init__.py
│   ├── mqtt_service.py
│   └── batch_service.py
└── tests/
    ├── test_sensors.py
    └── test_batch.py
```

### requirements.txt

```
fastapi==0.109.0
uvicorn[standard]==0.27.0
paho-mqtt==2.0.0
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
pydantic==2.5.3
pydantic-settings==2.1.0
python-dotenv==1.0.0
# MSSQL geçişi için (şimdilik yorum satırı):
# pyodbc==5.1.0
```

### .env

```bash
# MQTT
MQTT_BROKER=localhost
MQTT_PORT=1883

# PostgreSQL (Aşama 1)
PG_HOST=localhost
PG_PORT=5432
PG_DB=fabrika_iot
PG_USER=fabrika_user
PG_PASSWORD=güçlü_şifre

# MSSQL (Aşama 2 — ileride doldur)
MSSQL_HOST=192.168.1.200
MSSQL_PORT=1433
MSSQL_DB=FabrikaIoT
MSSQL_USER=iot_user
MSSQL_PASSWORD=

# Aktif veritabanı
ACTIVE_DB=postgresql
```

---

## 14. v2 → v3 Değişiklik Özeti

```
DEĞİŞEN / DÜZELTILEN:

  [Grafana Kurulum]
  ✗ ESKİ: apt install software-properties-common  → Bookworm'da yok!
  ✗ ESKİ: apt-key add (deprecated)
  ✓ YENİ: gpg --dearmor ile /etc/apt/keyrings/grafana.gpg
  ✓ YENİ: [signed-by=...] ile repo ekleme

  [Python Paket Kurulumu]
  ✗ ESKİ: pip3 install fastapi ...
  ✓ YENİ: pip3 install --break-system-packages fastapi ...
            (Bookworm externally-managed-environment hatası için)

  [Ağ Yönetimi]
  ✗ ESKİ: /etc/dhcpcd.conf + systemctl restart dhcpcd
  ✓ YENİ: nmcli connection modify + ipv4.route-metric
            (Bookworm'da dhcpcd.service yok)

  [PostgreSQL Schema Yükleme]
  ✗ ESKİ: sudo -u postgres psql -f /home/server/.../schema.sql
            → Permission denied hatası
  ✓ YENİ: cp schema.sql /tmp/ && sudo -u postgres psql -f /tmp/schema.sql
            (postgres kullanıcısı /home/server dizinine erişemez)

  [Partition Tarihleri]
  ✗ ESKİ: 2024-2025 partition'ları
  ✓ YENİ: 2026 partition'ları (güncel yıl)
```
