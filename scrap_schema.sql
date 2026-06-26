-- ════════════════════════════════════════════════════════════
--  FİRE TAKİP ŞEMASI
--  scrap_reasons  → fire sebepleri (duruş sebeplerinden AYRI liste)
--  scrap_entries  → elle girilen fire kayıtları (adet + sebep + zaman)
--
--  Çalıştırma (postgres kullanıcısı uzaktan dosya okuyamadığı için /tmp'ye kopyala):
--    cp scrap_schema.sql /tmp/
--    sudo -u postgres psql -d cerkezkoy_db -f /tmp/scrap_schema.sql
-- ════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS scrap_reasons (
    reason_id     SERIAL PRIMARY KEY,
    reason_code   VARCHAR(40)  UNIQUE NOT NULL,
    reason_name   VARCHAR(80)  NOT NULL,
    category      VARCHAR(20)  DEFAULT 'quality',   -- quality / setup / material / other
    color_hex     VARCHAR(9)   DEFAULT '#ef4444',
    icon          VARCHAR(40)  DEFAULT '',
    display_order INT          DEFAULT 0,
    is_active     BOOLEAN      DEFAULT TRUE,
    created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scrap_entries (
    id            SERIAL PRIMARY KEY,
    line_id       INT          NOT NULL,
    reason_id     INT          NOT NULL REFERENCES scrap_reasons(reason_id),
    qty           INT          NOT NULL CHECK (qty > 0),
    shift         VARCHAR(20),
    operator_name VARCHAR(80),
    notes         TEXT,
    source        VARCHAR(20)  DEFAULT 'terminal',  -- terminal / dashboard
    created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scrap_entries_line_date ON scrap_entries (line_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scrap_entries_reason    ON scrap_entries (reason_id);

-- ── Pres üretimine uygun fire sebepleri (istediğin gibi düzenle) ──
INSERT INTO scrap_reasons (reason_code, reason_name, category, color_hex, display_order) VALUES
  ('OLCU_DISI',   'Ölçü Dışı',      'quality',  '#ef4444', 1),
  ('YUZEY',       'Yüzey Hatası',   'quality',  '#f59e0b', 2),
  ('CAPAK',       'Çapak',          'quality',  '#8b5cf6', 3),
  ('CATLAK',      'Çatlak / Kırık', 'quality',  '#dc2626', 4),
  ('EZIK_CIZIK',  'Ezik / Çizik',   'quality',  '#f97316', 5),
  ('AYAR_FIRESI', 'Ayar Firesi',    'setup',    '#3b82f6', 6),
  ('MALZEME',     'Malzeme Hatası', 'material', '#06b6d4', 7),
  ('DIGER',       'Diğer',          'other',    '#64748b', 9)
ON CONFLICT (reason_code) DO NOTHING;
