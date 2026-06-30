-- ════════════════════════════════════════════════════════════
--  VARDİYA + OEE TEK KAYNAK — Adım 1 (altyapı, davranış değişmez)
--  cp shift_oee_schema.sql /tmp/
--  sudo -u postgres psql -d cerkezkoy_db -f /tmp/shift_oee_schema.sql
-- ════════════════════════════════════════════════════════════

-- ── 1) Vardiya tanımı: tek kaynak ──
-- window_hours   = detect_shift'in kullandığı PENCERE genişliği (saat sınırı)
-- planned_seconds= OEE paydası TABANI (mola dinamik olarak buradan düşülecek)
-- Şu an tek vardiya: 08–18 penceresi, taban 10 saat (mola çalışma anında düşülür)
CREATE TABLE IF NOT EXISTS shift_config (
    code            VARCHAR(20) PRIMARY KEY,   -- 'vardiya_1'
    label           VARCHAR(40) NOT NULL,      -- '1. Vardiya'
    start_hour      INT  NOT NULL,             -- 8
    end_hour        INT  NOT NULL,             -- 18  (24 = gece yarısı)
    latest_end      INT  NOT NULL,             -- özet yazma saati (19)
    window_hours    INT  NOT NULL,             -- 10  (end-start; detect için)
    planned_seconds INT  NOT NULL,             -- 36000 (OEE payda tabanı)
    display_order   INT  DEFAULT 0,
    is_active       BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Tek vardiya seed — MEVCUT davranışı birebir yansıtır (payda tabanı 10 saat)
-- Mola dinamik düşüleceği için taban 10 saat; gerçek payda = 10s - mola.
INSERT INTO shift_config
    (code, label, start_hour, end_hour, latest_end, window_hours, planned_seconds, display_order)
VALUES
    ('vardiya_1', '1. Vardiya', 8, 18, 19, 10, 36000, 1)
ON CONFLICT (code) DO NOTHING;

-- ── 2) OEE'den hariç bayrağı: mola + vardiya sonu tek mekanizma ──
ALTER TABLE downtime_reasons
    ADD COLUMN IF NOT EXISTS exclude_from_oee BOOLEAN DEFAULT FALSE;

-- Yok sayılan zaman = paydadan düşülür, normal duruş sayılmaz
UPDATE downtime_reasons
   SET exclude_from_oee = TRUE
 WHERE reason_code IN ('VARDIYA_SONU', 'MOLA_YEMEK', 'MOLA_CAY', 'MOLA_NAMAZ');

-- ── 3) Config reload zamanı (servisler cache geçersiz kılma için okur) ──
INSERT INTO system_settings (key, value)
VALUES ('config_version', extract(epoch from now())::bigint::text)
ON CONFLICT (key) DO NOTHING;