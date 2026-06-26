-- ============================================================
-- DURUŞ TAKİP — PostgreSQL Migration
-- Pi 3B'de çalıştır:
--   cp 01_downtimes_migration.sql /tmp/
--   sudo -u postgres psql -d fabrika_iot -f /tmp/01_downtimes_migration.sql
-- ============================================================

-- Duruş sebepleri tablosu (lookup)
CREATE TABLE IF NOT EXISTS downtime_reasons (
    reason_id       SERIAL PRIMARY KEY,
    reason_code     VARCHAR(20)  NOT NULL UNIQUE,   -- 'ARIZA', 'KALIP', 'MALZEME' vb.
    reason_name     VARCHAR(100) NOT NULL,           -- Türkçe açıklama
    category        VARCHAR(50)  NOT NULL DEFAULT 'planned',  -- 'planned' / 'unplanned'
    color_hex       VARCHAR(7)   DEFAULT '#EF4444',  -- UI'da buton rengi
    icon            VARCHAR(50)  DEFAULT 'wrench',   -- UI'da ikon adı
    display_order   INTEGER      DEFAULT 0,
    is_active       BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Duruş kayıtları tablosu
CREATE TABLE IF NOT EXISTS downtimes (
    downtime_id     BIGSERIAL    PRIMARY KEY,
    line_id         INTEGER      NOT NULL REFERENCES production_lines(line_id),
    machine_id      INTEGER      REFERENCES machines(machine_id),  -- NULL olabilir (hat geneli)
    reason_id       INTEGER      NOT NULL REFERENCES downtime_reasons(reason_id),
    operator_name   VARCHAR(100),
    shift           VARCHAR(20),                     -- 'vardiya_1', 'vardiya_2', 'vardiya_3'
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,                     -- NULL = hâlâ devam ediyor
    duration_sec    INTEGER,                         -- ended_at - started_at (saniye)
    notes           TEXT,                            -- Operatör notu
    is_active       BOOLEAN      DEFAULT TRUE,       -- Aktif duruş mu?
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- İndeksler
CREATE INDEX IF NOT EXISTS idx_downtimes_line_active
    ON downtimes (line_id, is_active) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_downtimes_started
    ON downtimes (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_downtimes_line_date
    ON downtimes (line_id, started_at);

-- Varsayılan duruş sebepleri
INSERT INTO downtime_reasons (reason_code, reason_name, category, color_hex, icon, display_order)
VALUES
    ('ARIZA',       'Makine Arızası',           'unplanned', '#EF4444', 'alert-triangle',  1),
    ('KALIP',       'Kalıp Değişimi',           'planned',   '#F59E0B', 'repeat',          2),
    ('MALZEME',     'Malzeme Bekleme',           'unplanned', '#8B5CF6', 'package',         3),
    ('BAKIM',       'Planlı Bakım',              'planned',   '#3B82F6', 'tool',            4),
    ('KALITE',      'Kalite Kontrol',            'planned',   '#10B981', 'check-circle',    5),
    ('SETUP',       'Ayar / Setup',              'planned',   '#6366F1', 'settings',        6),
    ('ELEKTRIK',    'Elektrik Kesintisi',         'unplanned', '#F97316', 'zap-off',         7),
    ('OPERATOR',    'Operatör Molası',            'planned',   '#14B8A6', 'coffee',          8),
    ('DIGER',       'Diğer',                     'unplanned', '#6B7280', 'help-circle',     9)
ON CONFLICT (reason_code) DO NOTHING;

-- fabrika_user yetkisi
GRANT ALL PRIVILEGES ON TABLE downtime_reasons TO yigitcanc;
GRANT ALL PRIVILEGES ON TABLE downtimes TO yigitcanc;
GRANT USAGE, SELECT ON SEQUENCE downtime_reasons_reason_id_seq TO yigitcanc;
GRANT USAGE, SELECT ON SEQUENCE downtimes_downtime_id_seq TO yigitcanc;

-- Kontrol
SELECT 'Migration OK — downtime_reasons: ' || COUNT(*) FROM downtime_reasons;
