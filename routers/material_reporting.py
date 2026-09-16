"""Material production/quality report shared by all screens and CSV export."""
import csv
import io
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from models.database import get_db
from services.material_service import report

router = APIRouter(prefix='/api/v1/material-report', tags=['Malzeme Raporu'])


@router.get('')
def material_report(line_id: int = Query(..., ge=1), start: date = Query(...),
                    end: date = Query(...), material_id: Optional[int] = Query(None, ge=0),
                    shift: Optional[str] = None, db: Session = Depends(get_db)):
    return report(db, line_id, start, end, material_id, shift)


@router.get('/csv')
def material_csv(line_id: int = Query(..., ge=1), start: date = Query(...),
                 end: date = Query(...), material_id: Optional[int] = Query(None, ge=0),
                 shift: Optional[str] = None, db: Session = Depends(get_db)):
    data = report(db, line_id, start, end, material_id, shift)
    out = io.StringIO(); writer = csv.writer(out, delimiter=';')
    writer.writerow(['Tarih','Vardiya','Malzeme','Adı','Üretim','Fire','Sağlam','Ayrılmamış vardiya firesi'])
    for r in data['items']:
        values = [r[k] for k in ('date','shift','material_code','material_name','produced','scrap','good','unallocated_shift_scrap')]
        writer.writerow([("'"+v if v.startswith(('=','+','-','@')) else v) if isinstance(v,str) else v for v in values])
    return Response('\ufeff'+out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="malzeme-uretim.csv"'})
