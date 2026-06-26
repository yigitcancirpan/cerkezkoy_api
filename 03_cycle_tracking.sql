-- ============================================================
-- İLK/SON BASKI + TAHMİNİ BİTİŞ — Ek Migration
-- cp 03_cycle_tracking.sql /tmp/
-- sudo -u postgres psql -d fabrika_iot -f /tmp/03_cycle_tracking.sql
-- ============================================================

-- production_current'a yeni kolonlar
ALTER TABLE production_current
    ADD COLUMN IF NOT EXISTS first_cycle_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_cycle_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS shift_start_produced INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS current_shift VARCHAR(20);

-- shift_summary'ye ilk/son baskı saatleri
ALTER TABLE shift_summary
    ADD COLUMN IF NOT EXISTS first_cycle_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_cycle_at TIMESTAMPTZ;

SELECT 'Cycle tracking columns OK';