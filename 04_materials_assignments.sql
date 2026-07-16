-- ═══════════════════════════════════════════════════════════
-- Malzeme + Pres Atama Şeması
-- 04_materials_assignments.sql
--
-- Kurulum:
--   psql -h localhost -U yigitcanc -d cerkezkoy_db -f 04_materials_assignments.sql
--
-- Mantık:
--   materials            → malzeme/model kartları (kod, ad, ideal cycle, hedef)
--   press_assignments    → pres ↔ malzeme eşleşmesi (aktif + geçmiş, izlenebilirlik)
--   Bir preste aynı anda TEK aktif atama olabilir (partial unique index ile DB garantisi)
-- ═══════════════════════════════════════════════════════════

-- ── 1) MALZEMELER ──
CREATE TABLE IF NOT EXISTS materials (
    material_id     SERIAL PRIMARY KEY,
    material_code   VARCHAR(50) UNIQUE NOT NULL,     -- örn: "QS-4420-A"
    material_name   VARCHAR(150) NOT NULL,           -- örn: "Qs Sağ Braket"
    model_id        INTEGER,                          -- PLC'deki model_id ile eşleşir (production_current.model_id)
    ideal_cycle_ds  INTEGER,                          -- ideal cycle (desisaniye, PLC formatıyla aynı: 90 = 9.0sn)
    default_target  INTEGER DEFAULT 0,                -- varsayılan vardiya hedefi
    notes           TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2) PRES ATAMALARI (aktif + geçmiş) ──
CREATE TABLE IF NOT EXISTS press_assignments (
    assignment_id   BIGSERIAL PRIMARY KEY,
    machine_id      INTEGER NOT NULL REFERENCES machines(machine_id),
    material_id     INTEGER NOT NULL REFERENCES materials(material_id),
    line_id         INTEGER NOT NULL DEFAULT 1,
    target          INTEGER DEFAULT 0,                -- bu atamaya özel hedef (0 = materials.default_target kullan)
    lot_number      INTEGER,
    operator_name   VARCHAR(100),
    shift           VARCHAR(20),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,                      -- NULL = hâlâ üretimde
    is_active       BOOLEAN DEFAULT TRUE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ⚠️ KRİTİK: Bir preste aynı anda tek aktif atama — DB seviyesinde garanti.
-- Uygulama katmanındaki kontrol yarış koşulunda delinebilir; bu index delinemez.
CREATE UNIQUE INDEX IF NOT EXISTS uq_press_active_assignment
    ON press_assignments (machine_id)
    WHERE is_active = TRUE;

-- Sorgu performansı (geçmiş + rapor)
CREATE INDEX IF NOT EXISTS idx_assignments_machine_time
    ON press_assignments (machine_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_assignments_material
    ON press_assignments (material_id, started_at DESC);

-- ── 3) BAŞLANGIÇ VERİSİ (3 pres zaten machines'te olmalı — kontrol) ──
-- Presler machines tablosunda yoksa ekle (line_id=1 = Qs Hattı varsayımı):
INSERT INTO machines (line_id, machine_name, machine_type, status)
SELECT 1, v.name, 'hydraulic_press', 'active'
FROM (VALUES ('Pres 1 (88)'), ('Pres 2 (95)'), ('Pres 3 (96)')) AS v(name)
WHERE NOT EXISTS (
    SELECT 1 FROM machines WHERE machine_name = v.name
);

-- Örnek malzemeler (istersen sil / kendi kodlarını gir):
INSERT INTO materials (material_code, material_name, model_id, ideal_cycle_ds, default_target)
VALUES
    ('QS-001', 'Qs Braket Sağ',  1, 90, 3000),
    ('QS-002', 'Qs Braket Sol',  2, 90, 3000),
    ('QS-003', 'Qs Taban Sacı',  3, 120, 2200)
ON CONFLICT (material_code) DO NOTHING;
