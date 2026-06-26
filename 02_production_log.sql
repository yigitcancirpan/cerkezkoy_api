-- ============================================================
-- ÜRETİM TAKİP — PostgreSQL Migration
-- Pi 3B'de çalıştır:
--   cp 02_production_log.sql /tmp/
--   sudo -u postgres psql -d fabrika_iot -f /tmp/02_production_log.sql
-- ============================================================

-- 1) Anlık üretim durumu (her MQTT mesajında güncellenir)
--    Sadece son durum — ekranda anlık gösterim için
CREATE TABLE IF NOT EXISTS production_current (
    line_id         INTEGER PRIMARY KEY REFERENCES production_lines(line_id),
    lot_number      INTEGER DEFAULT 1,
    model_id        INTEGER DEFAULT 0,
    target          INTEGER DEFAULT 0,
    produced        INTEGER DEFAULT 0,
    scrap           INTEGER DEFAULT 0,
    good            INTEGER DEFAULT 0,
    actual_cycle    INTEGER DEFAULT 0,      -- saniyenin 10'da biri (82 = 8.2sn)
    average_cycle   INTEGER DEFAULT 0,
    last_5_cycles   TEXT DEFAULT '[]',      -- JSON array
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 2) Üretim logu (her 1 dakikada bir snapshot)
--    Grafana grafikleri ve vardiya raporları için
CREATE TABLE IF NOT EXISTS production_log (
    log_id          BIGSERIAL PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES production_lines(line_id),
    lot_number      INTEGER,
    model_id        INTEGER,
    target          INTEGER DEFAULT 0,
    produced        INTEGER DEFAULT 0,
    scrap           INTEGER DEFAULT 0,
    good            INTEGER DEFAULT 0,
    actual_cycle    INTEGER DEFAULT 0,
    average_cycle   INTEGER DEFAULT 0,
    shift           VARCHAR(20),
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prodlog_line_time
    ON production_log (line_id, logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_prodlog_shift
    ON production_log (line_id, shift, logged_at);

-- 3) Vardiya özeti (vardiya bitiminde hesaplanır veya sorguyla)
CREATE TABLE IF NOT EXISTS shift_summary (
    summary_id      BIGSERIAL PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES production_lines(line_id),
    shift_date      DATE NOT NULL,
    shift           VARCHAR(20) NOT NULL,       -- vardiya_1, vardiya_2, vardiya_3
    model_id        INTEGER,
    target          INTEGER DEFAULT 0,
    total_produced  INTEGER DEFAULT 0,
    total_scrap     INTEGER DEFAULT 0,
    total_good      INTEGER DEFAULT 0,
    avg_cycle_time  REAL DEFAULT 0,
    total_downtime_sec INTEGER DEFAULT 0,       -- duruş toplamı
    oee_availability REAL,                       -- %
    oee_performance  REAL,                       -- %
    oee_quality      REAL,                       -- %
    oee_overall      REAL,                       -- %
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(line_id, shift_date, shift)
);

-- İlk kayıt: Hat 1 için current oluştur
INSERT INTO production_current (line_id) VALUES (1)
ON CONFLICT (line_id) DO NOTHING;

-- Yetki
GRANT ALL ON TABLE production_current TO yigitcanc;
GRANT ALL ON TABLE production_log TO yigitcanc;
GRANT ALL ON TABLE shift_summary TO yigitcanc;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO yigitcanc;

SELECT 'Production tables OK';
