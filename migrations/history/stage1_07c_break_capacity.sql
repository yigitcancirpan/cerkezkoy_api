\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';

DO $$ BEGIN
 IF current_database() <> 'cerkezkoy_db' THEN RAISE EXCEPTION 'Yanlış veritabanı'; END IF;
 IF to_regclass('public.planning_auto_runs') IS NULL THEN RAISE EXCEPTION 'Önce 1.07b kurulmalı'; END IF;
 IF to_regclass('public.break_schedules') IS NULL THEN RAISE EXCEPTION 'Mola tablosu bulunamadı'; END IF;
END $$;

ALTER TABLE public.planning_daily_capacity
 ADD COLUMN IF NOT EXISTS break_work boolean NOT NULL DEFAULT false,
 ADD COLUMN IF NOT EXISTS break_work_minutes integer NOT NULL DEFAULT 0 CHECK (break_work_minutes BETWEEN 0 AND 240),
 ADD COLUMN IF NOT EXISTS break_extra_qty integer NOT NULL DEFAULT 0 CHECK (break_extra_qty >= 0);

COMMENT ON COLUMN public.planning_daily_capacity.break_work IS
 'Onaylı planda hattın dönüşümlü ekiple mola pencerelerinde çalışması';
COMMENT ON COLUMN public.planning_daily_capacity.break_work_minutes IS
 'Planlanan toplam dönüşümlü mola çalışma süresi';
COMMENT ON COLUMN public.planning_daily_capacity.break_extra_qty IS
 'Mola dışı kapasitenin üzerinde planlanan sağlam adet';

INSERT INTO public.break_schedules
 (shift_code, break_code, label, start_minute, end_minute, line_id, day_of_week, is_active)
VALUES
 ('vardiya_1', 'PLANLAMA_AKSAM_YEMEK', 'Akşam Yemek Molası', 1080, 1110, 2, NULL, TRUE)
ON CONFLICT (shift_code, break_code, line_id, day_of_week)
DO UPDATE SET label=EXCLUDED.label, start_minute=EXCLUDED.start_minute,
              end_minute=EXCLUDED.end_minute, is_active=TRUE, updated_at=NOW();

COMMIT;

SELECT break_code, label, start_minute, end_minute, line_id, is_active
FROM public.break_schedules
WHERE shift_code='vardiya_1' AND break_code='PLANLAMA_AKSAM_YEMEK' AND line_id=2;

SELECT column_name
FROM information_schema.columns
WHERE table_schema='public' AND table_name='planning_daily_capacity'
  AND column_name IN ('break_work','break_work_minutes','break_extra_qty')
ORDER BY column_name;
