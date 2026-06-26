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