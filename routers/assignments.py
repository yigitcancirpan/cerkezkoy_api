"""
Pres ↔ Malzeme Atama API
routers/assignments.py

Endpoint'ler:
  GET    /api/v1/assignments/board             → Başlangıç ekranı tek çağrı: presler + aktif atamalar + malzemeler
  GET    /api/v1/assignments/materials         → Malzeme listesi (aktifler)
  POST   /api/v1/assignments/materials         → Yeni malzeme ekle
  PATCH  /api/v1/assignments/materials/{id}    → Malzeme güncelle
  POST   /api/v1/assignments/assign            → Prese malzeme ata (öncekini otomatik kapatır)
  POST   /api/v1/assignments/{id}/end          → Atamayı bitir (pres boşa çıkar)
  GET    /api/v1/assignments/history           → Atama geçmişi (izlenebilirlik)

main.py'a ekle:
    from routers import assignments
    app.include_router(assignments.router)

Mimari notlar (proje konvansiyonlarıyla uyumlu):
  - Prefix router İÇİNDE tanımlı, main.py prefix'siz include eder
  - text() + parametrik SQL (injection-safe)
  - Tüm zaman damgaları timezone-aware (timezone.utc)
  - Atama değişince MQTT 'fabrika/hat1/atama' topic'ine retained mesaj →
    terminal (.51) ve plc_publisher (Pi4) anlık haberdar olur, restart gerekmez
    (shift_config reload paterninin aynısı)
  - Tek aktif atama garantisi: uygulama kontrolü + DB partial unique index
    (uq_press_active_assignment) — yarış koşulunda IntegrityError yakalanır
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from models.database import get_db

router = APIRouter(prefix="/api/v1/assignments", tags=["Pres Atama"])
import logging
logger = logging.getLogger("assignments")

# ─── MQTT bildirim (settings.py'deki _publish_config_reload paterni) ───
def _publish_assignment_change(machine_id: int, material: Optional[dict], line_id: int = 1):
    """Atama değişti → terminal + ilgili hattın publisher'ı anlık haberdar olsun."""
    prefix = "fabrika/hat2" if line_id == 2 else "fabrika/hat1"
    payload = {
        "machine_id": machine_id,
        "material": material,                       # geriye uyumlu (terminal + hat1)
        "target":     (material or {}).get("target", 0),      # pulse_publisher ÜST seviye okur
        "lot_number": (material or {}).get("lot_number", 0),
        "model_id":   (material or {}).get("model_id", 0),
        "ts": time.time(),
    }
    try:
        import paho.mqtt.publish as publish
        publish.single(f"{prefix}/atama", payload=json.dumps(payload),
                       hostname="127.0.0.1", port=1883, retain=True)
    except Exception as e:
        logger.warning(f"atama MQTT yayını başarısız (DB yazıldı, polling yakalar): {e}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─── ŞEMALAR ───
class MaterialCreate(BaseModel):
    material_code: str
    material_name: str
    model_id: Optional[int] = None
    ideal_cycle_ds: Optional[int] = Field(None, ge=1, le=6000)  # 0.1sn–600sn
    default_target: int = Field(0, ge=0)
    notes: Optional[str] = None


class MaterialUpdate(BaseModel):
    material_name: Optional[str] = None
    model_id: Optional[int] = None
    ideal_cycle_ds: Optional[int] = Field(None, ge=1, le=6000)
    default_target: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class AssignRequest(BaseModel):
    machine_id: int
    material_id: int
    target: Optional[int] = Field(None, ge=0)   # boşsa materials.default_target
    lot_number: Optional[int] = None
    operator_name: Optional[str] = None
    notes: Optional[str] = None


class EndRequest(BaseModel):
    notes: Optional[str] = None


# ─── 1) BOARD: Başlangıç ekranı tek çağrıda ───
@router.get("/board")
def get_board(line_id: int = Query(1), db: Session = Depends(get_db)):
    """
    Başlangıç ekranı için her şey tek istekte:
      - Hattaki presler + varsa aktif ataması (malzeme, hedef, süre)
      - Seçilebilir aktif malzemeler
    Frontend tek fetch ile ekranı kurar; Pi 3B'de round-trip azalır.
    """
    presses = db.execute(text("""
        SELECT m.machine_id, m.machine_name, m.status,
               a.assignment_id, a.material_id, a.target, a.lot_number,
               a.operator_name, a.started_at,
               mat.material_code, mat.material_name, mat.ideal_cycle_ds,
               EXTRACT(EPOCH FROM (NOW() - a.started_at))::int AS running_sec
        FROM machines m
        LEFT JOIN press_assignments a
               ON a.machine_id = m.machine_id AND a.is_active = TRUE
        LEFT JOIN materials mat ON mat.material_id = a.material_id
        WHERE m.line_id = :lid AND m.machine_type IN ('hydraulic_press','mechanical_press')
        ORDER BY m.machine_id
    """), {"lid": line_id}).fetchall()

    materials = db.execute(text("""
        SELECT material_id, material_code, material_name, model_id,
               ideal_cycle_ds, default_target
        FROM materials
        WHERE is_active = TRUE
        ORDER BY material_code
    """)).fetchall()

    # Hangi malzemeler şu an bir preste? (çift atama önerisini engellemek için işaretle)
    in_production = {r.material_id for r in presses if r.material_id is not None}

    return {
        "presses": [
            {
                "machine_id": r.machine_id,
                "machine_name": r.machine_name,
                "status": r.status,
                "assignment": None if r.assignment_id is None else {
                    "assignment_id": r.assignment_id,
                    "material_id": r.material_id,
                    "material_code": r.material_code,
                    "material_name": r.material_name,
                    "target": r.target,
                    "lot_number": r.lot_number,
                    "operator_name": r.operator_name,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "running_sec": max(r.running_sec or 0, 0),
                    "ideal_cycle_ds": r.ideal_cycle_ds,
                },
            }
            for r in presses
        ],
        "materials": [
            {
                "material_id": m.material_id,
                "material_code": m.material_code,
                "material_name": m.material_name,
                "model_id": m.model_id,
                "ideal_cycle_ds": m.ideal_cycle_ds,
                "default_target": m.default_target,
                "in_production": m.material_id in in_production,
            }
            for m in materials
        ],
        "fetched_at": _now_utc().isoformat(),
    }


# ─── 2) MALZEME CRUD ───
@router.get("/materials")
def list_materials(include_inactive: bool = Query(False), db: Session = Depends(get_db)):
    where = "" if include_inactive else "WHERE is_active = TRUE"
    rows = db.execute(text(f"""
        SELECT material_id, material_code, material_name, model_id,
               ideal_cycle_ds, default_target, notes, is_active
        FROM materials {where}
        ORDER BY material_code
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/materials", status_code=201)
def create_material(req: MaterialCreate, db: Session = Depends(get_db)):
    code = req.material_code.strip().upper()
    if not code:
        raise HTTPException(400, "Malzeme kodu boş olamaz")
    if db.execute(text("SELECT 1 FROM materials WHERE material_code = :c"),
                  {"c": code}).fetchone():
        raise HTTPException(409, f"Bu kod zaten var: {code}")
    row = db.execute(text("""
        INSERT INTO materials
            (material_code, material_name, model_id, ideal_cycle_ds, default_target, notes)
        VALUES (:c, :n, :mid, :cyc, :tgt, :notes)
        RETURNING material_id
    """), {"c": code, "n": req.material_name, "mid": req.model_id,
           "cyc": req.ideal_cycle_ds, "tgt": req.default_target,
           "notes": req.notes}).fetchone()
    db.commit()
    return {"material_id": row[0], "material_code": code, "message": "Malzeme eklendi"}


@router.patch("/materials/{material_id}")
def update_material(material_id: int, req: MaterialUpdate, db: Session = Depends(get_db)):
    # scrap.py'deki [H-4] bulgusundaki gibi allowlist ile — dinamik alan adı SQL'e sızamaz
    ALLOWED = {"material_name", "model_id", "ideal_cycle_ds",
               "default_target", "notes", "is_active"}
    fields = {k: v for k, v in req.dict().items() if v is not None and k in ALLOWED}
    if not fields:
        raise HTTPException(400, "Güncellenecek alan yok")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["mid"] = material_id
    res = db.execute(
        text(f"UPDATE materials SET {sets}, updated_at = NOW() "
             f"WHERE material_id = :mid RETURNING material_id"),
        fields
    ).fetchone()
    db.commit()
    if not res:
        raise HTTPException(404, "Malzeme bulunamadı")
    return {"material_id": res[0], "message": "Güncellendi"}


# ─── 3) ATAMA ───
@router.post("/assign", status_code=201)
def assign_material(req: AssignRequest, db: Session = Depends(get_db)):
    """
    Prese malzeme ata. Preste zaten aktif atama varsa önce onu kapatır
    (malzeme değişimi = eski atama biter, yeni başlar → izlenebilirlik korunur).
    """
    # Pres kontrolü
    machine = db.execute(text("""
        SELECT machine_id, machine_name, line_id FROM machines
        WHERE machine_id = :mid AND machine_type IN ('hydraulic_press','mechanical_press')
    """), {"mid": req.machine_id}).fetchone()
    if not machine:
        raise HTTPException(404, f"Pres bulunamadı: machine_id={req.machine_id}")

    # Malzeme kontrolü
    material = db.execute(text("""
        SELECT material_id, material_code, material_name, model_id,
               ideal_cycle_ds, default_target
        FROM materials WHERE material_id = :mid AND is_active = TRUE
    """), {"mid": req.material_id}).fetchone()
    if not material:
        raise HTTPException(404, f"Aktif malzeme bulunamadı: material_id={req.material_id}")

    now = _now_utc()

    # Vardiya tespiti — shift_utils tek kaynak (hardcode saat YOK)
    import shift_utils
    shift_code = shift_utils.detect_shift_code()

    # Önceki aktif atamayı kapat (varsa) — malzeme değişimi senaryosu
    prev = db.execute(text("""
        UPDATE press_assignments
        SET is_active = FALSE, ended_at = :now,
            notes = COALESCE(notes, '') || ' [yeni atama ile kapatıldı]'
        WHERE machine_id = :mid AND is_active = TRUE
        RETURNING assignment_id
    """), {"mid": req.machine_id, "now": now}).fetchone()

    target = req.target if req.target else (material.default_target or 0)

    try:
        row = db.execute(text("""
            INSERT INTO press_assignments
                (machine_id, material_id, line_id, target, lot_number,
                 operator_name, shift, started_at, is_active, notes)
            VALUES (:mach, :mat, :lid, :tgt, :lot, :op, :shift, :now, TRUE, :notes)
            RETURNING assignment_id
        """), {"mach": req.machine_id, "mat": req.material_id,
               "lid": machine.line_id, "tgt": target, "lot": req.lot_number,
               "op": req.operator_name, "shift": shift_code,
               "now": now, "notes": req.notes}).fetchone()
        db.commit()
    except IntegrityError:
        # DB partial unique index yarış koşulunu yakaladı — başka istek önce davrandı
        db.rollback()
        raise HTTPException(409, "Bu prese eş zamanlı başka bir atama yapıldı, sayfayı yenileyin")

    # MQTT bildirim — terminal + plc_publisher anlık öğrenir
    _publish_assignment_change(req.machine_id, {
        "material_id": material.material_id,
        "material_code": material.material_code,
        "material_name": material.material_name,
        "model_id": material.model_id,
        "ideal_cycle_ds": material.ideal_cycle_ds,
        "target": target,
        "lot_number": req.lot_number,
    }, line_id=machine.line_id)          # ← eklendi

    return {
        "assignment_id": row[0],
        "machine_name": machine.machine_name,
        "material_code": material.material_code,
        "material_name": material.material_name,
        "target": target,
        "previous_closed": prev[0] if prev else None,
        "message": f"{machine.machine_name} → {material.material_name} atandı",
    }


@router.post("/{assignment_id}/end")
def end_assignment(assignment_id: int, req: EndRequest = EndRequest(),
                   db: Session = Depends(get_db)):
    """Atamayı bitir — pres boşa çıkar."""
    now = _now_utc()
    row = db.execute(text("""
        UPDATE press_assignments
        SET is_active = FALSE, ended_at = :now,
            notes = COALESCE(notes, '') || :extra
        WHERE assignment_id = :aid AND is_active = TRUE
        RETURNING machine_id, line_id
    """), {"aid": assignment_id, "now": now,
           "extra": f" [Kapanış] {req.notes}" if req.notes else " [manuel bitirildi]"
           }).fetchone()
    db.commit()
    if not row:
        raise HTTPException(404, "Aktif atama bulunamadı")

    _publish_assignment_change(row[0], None, line_id=row[1])   # ← line_id eklendi
    return {"assignment_id": assignment_id, "machine_id": row[0],
            "message": "Atama bitirildi — pres boşta"}


# ─── 4) GEÇMİŞ (izlenebilirlik) ───
@router.get("/history")
def assignment_history(
    machine_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    clauses, params = [], {"lim": limit}
    if machine_id:
        clauses.append("a.machine_id = :mach"); params["mach"] = machine_id
    if material_id:
        clauses.append("a.material_id = :mat"); params["mat"] = material_id
    if date_from:
        clauses.append("a.started_at >= :df::date"); params["df"] = date_from
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = db.execute(text(f"""
        SELECT a.assignment_id, a.machine_id, m.machine_name,
               a.material_id, mat.material_code, mat.material_name,
               a.target, a.lot_number, a.operator_name, a.shift,
               a.started_at, a.ended_at, a.is_active, a.notes,
               EXTRACT(EPOCH FROM (COALESCE(a.ended_at, NOW()) - a.started_at))::int AS duration_sec
        FROM press_assignments a
        JOIN machines m ON m.machine_id = a.machine_id
        JOIN materials mat ON mat.material_id = a.material_id
        {where}
        ORDER BY a.started_at DESC
        LIMIT :lim
    """), params).fetchall()
    return {"items": [dict(r._mapping) for r in rows]}
