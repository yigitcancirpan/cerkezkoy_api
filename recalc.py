# ~/cerkezkoy_api/recalc.py
import os, logging
from datetime import date, timedelta
from services.production_logger import ProductionLogger
import shift_utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

svc = ProductionLogger(db_url=os.environ["DB_URL"])
LINE_ID = 2
START = date(2026, 8, 1)
END   = date.today() - timedelta(days=1)   # bugünü dahil etme, vardiya bitmedi

d = START
while d <= END:
    for sh in shift_utils.get_shifts():
        svc._write_shift_summary(LINE_ID, sh["code"], d)
    d += timedelta(days=1)
print("bitti")