"""
Geçmiş vardiya özetlerini production_log'dan üretip shift_summary'e yazar.
Bugünü (1 Haziran) hariç tutar.
TEK SEFERLİK çalıştırılır.
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# production_logger modülünü import edebilmek için path ekle
sys.path.insert(0, os.path.expanduser("~/cerkezkoy_api"))
from services.production_logger import ProductionLogger

shifts = [
    {"code": "vardiya_1", "label": "1. Vardiya",
     "start_hour": 8, "end_hour": 18, "earliest_end": 17, "latest_end": 19},
    {"code": "vardiya_2", "label": "2. Vardiya",
     "start_hour": 18, "end_hour": 24, "earliest_end": 23, "latest_end": 1},
]

svc = ProductionLogger(
    db_url=os.getenv("DB_URL", "postgresql://yigitcanc:***REMOVED***@127.0.0.1:5432/cerkezkoy_db"),
    shifts_config=shifts,
)

# 30 gün geri git — bugün hariç tutulur çünkü _resolve_shift_date
# henüz bitmemiş vardiyalar için None döner
svc._catch_up_missing_summaries(days_back=30)

print("✓ Backfill tamamlandı")