-- Aşama 1.03 — OEE v3, ürün çevrimi ve gerçek mola pencereleri
-- Mevcut üretim/duruş/fire satırlarını silmez.

BEGIN;

ALTER TABLE public.downtime_reasons
    ADD COLUMN IF NOT EXISTS oee_category VARCHAR(30)
        NOT NULL DEFAULT 'downtime_loss',
    ADD COLUMN IF NOT EXISTS exclude_from_oee BOOLEAN
        NOT NULL DEFAULT FALSE;

ALTER TABLE public.shift_summary
    ADD COLUMN IF NOT EXISTS formula_version VARCHAR(20),
    ADD COLUMN IF NOT EXISTS calculated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS planned_base_sec INTEGER,
    ADD COLUMN IF NOT EXISTS planned_production_sec INTEGER,
    ADD COLUMN IF NOT EXISTS run_time_sec INTEGER,
    ADD COLUMN IF NOT EXISTS ideal_cycle_sec NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS ideal_time_sec NUMERIC(14,3),
    ADD COLUMN IF NOT EXISTS ideal_cycle_detail JSONB;

UPDATE public.shift_summary
SET formula_version = COALESCE(formula_version, 'legacy-v1'),
    calculated_at = COALESCE(calculated_at, created_at)
WHERE formula_version IS NULL OR calculated_at IS NULL;

COMMENT ON COLUMN public.shift_summary.formula_version IS
    'OEE hesaplama formülü sürümü (legacy-v1, oee-v3 vb.)';
COMMENT ON COLUMN public.shift_summary.calculated_at IS
    'Vardiya özetinin en son hesaplandığı zaman';
COMMENT ON COLUMN public.shift_summary.planned_base_sec IS
    'Hesap sırasında kullanılan brüt vardiya süresi';
COMMENT ON COLUMN public.shift_summary.planned_production_sec IS
    'OEE dışı süreler çıkarıldıktan sonraki planlanan üretim süresi';
COMMENT ON COLUMN public.shift_summary.run_time_sec IS
    'Planlanan üretim süresinden plansız duruşlar çıkarıldıktan sonraki süre';
COMMENT ON COLUMN public.shift_summary.ideal_cycle_sec IS
    'Performans hesabında kullanılan ideal çevrim süresi (saniye/adet)';
COMMENT ON COLUMN public.shift_summary.ideal_time_sec IS
    'Toplam üretim ile ideal çevrim süresinin çarpımı';
COMMENT ON COLUMN public.shift_summary.ideal_cycle_detail IS
    'Model/malzeme bazında adet, ideal çevrim ve kaynak dökümü';

CREATE TABLE IF NOT EXISTS public.break_schedules (
    break_id BIGSERIAL PRIMARY KEY,
    shift_code VARCHAR(30) NOT NULL,
    break_code VARCHAR(30) NOT NULL,
    label VARCHAR(80) NOT NULL,
    start_minute SMALLINT NOT NULL CHECK (start_minute BETWEEN 0 AND 1439),
    end_minute SMALLINT NOT NULL CHECK (end_minute BETWEEN 1 AND 1440),
    line_id INTEGER REFERENCES public.production_lines(line_id),
    day_of_week SMALLINT CHECK (day_of_week BETWEEN 0 AND 6),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_minute > start_minute)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_break_schedule_scope
ON public.break_schedules (
    shift_code,
    break_code,
    line_id,
    day_of_week
) NULLS NOT DISTINCT;

INSERT INTO public.break_schedules
    (shift_code, break_code, label, start_minute, end_minute,
     line_id, day_of_week, is_active)
VALUES
    ('vardiya_1', 'CAY_SABAH', 'Sabah Çay Molası', 600, 615, NULL, NULL, TRUE),
    ('vardiya_1', 'YEMEK', 'Yemek Molası', 720, 750, NULL, NULL, TRUE),
    ('vardiya_1', 'CAY_OGLESONRA', 'Öğleden Sonra Çay Molası', 900, 915, NULL, NULL, TRUE),
    ('vardiya_1', 'CAY_OGLESONRA', 'Cumartesi Çay Molası', 840, 855, NULL, 6, TRUE)
ON CONFLICT (shift_code, break_code, line_id, day_of_week)
DO NOTHING;

DO $operator_reason_guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.downtime_reasons
        WHERE reason_code='OPERATOR'
    ) THEN
        RAISE EXCEPTION
            'OPERATOR duruş sebebi bulunamadı; migration uygulanmadı';
    END IF;
END
$operator_reason_guard$;

UPDATE public.downtime_reasons
SET category='planned',
    oee_category='planned_stop',
    exclude_from_oee=TRUE
WHERE reason_code='OPERATOR';

DO $grant_runtime$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='yigitcanc2') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON public.break_schedules TO yigitcanc2;
        GRANT USAGE, SELECT
            ON SEQUENCE public.break_schedules_break_id_seq TO yigitcanc2;
    END IF;
END
$grant_runtime$;

COMMIT;
