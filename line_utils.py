"""line_utils.py — hat konfigürasyonunun tek kaynağı: production_lines tablosu."""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

def load_lines_config(db_url: str = None):
    url = db_url or os.environ["DB_URL"]
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT line_id, line_name, mqtt_prefix
            FROM production_lines
            WHERE is_active = TRUE AND mqtt_prefix IS NOT NULL
            ORDER BY line_id
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()