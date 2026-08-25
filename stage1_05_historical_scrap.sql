\set ON_ERROR_STOP on

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.scrap_entries
    ADD COLUMN IF NOT EXISTS production_date DATE;

UPDATE public.scrap_entries
SET production_date = (created_at AT TIME ZONE 'Europe/Istanbul')::date
WHERE production_date IS NULL;

UPDATE public.scrap_entries
SET shift = 'vardiya_1'
WHERE shift IS NULL OR BTRIM(shift) = '';

ALTER TABLE public.scrap_entries
    ALTER COLUMN production_date SET DEFAULT CURRENT_DATE,
    ALTER COLUMN production_date SET NOT NULL,
    ALTER COLUMN shift SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scrap_entries_production
    ON public.scrap_entries (line_id, production_date, shift);

COMMENT ON COLUMN public.scrap_entries.production_date IS
    'Firenin ait olduğu üretim/vardiya tarihi; created_at gerçek giriş zamanıdır';

COMMIT;

SELECT
    COUNT(*) AS total_entries,
    COUNT(*) FILTER (WHERE production_date IS NULL) AS missing_production_date,
    COUNT(*) FILTER (WHERE shift IS NULL OR BTRIM(shift) = '') AS missing_shift,
    MIN(production_date) AS first_production_date,
    MAX(production_date) AS last_production_date
FROM public.scrap_entries;

SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'scrap_entries'
ORDER BY indexname;
