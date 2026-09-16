\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $$ BEGIN
 IF current_database() <> 'cerkezkoy_db' THEN RAISE EXCEPTION 'Yanlış veritabanı'; END IF;
 IF to_regclass('public.planning_raw_orders') IS NULL THEN RAISE EXCEPTION 'Önce 1.06a kurulmalı'; END IF;
END $$;
CREATE TABLE public.planning_auto_runs (
 run_id uuid PRIMARY KEY,
 line_id integer NOT NULL,
 product_code varchar(50) NOT NULL,
 start_date date NOT NULL,
 end_date date NOT NULL CHECK(end_date >= start_date AND end_date-start_date < 56),
 request jsonb NOT NULL,
 result jsonb NOT NULL,
 input_fingerprint varchar(64) NOT NULL,
 status varchar(12) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved')),
 created_at timestamptz NOT NULL DEFAULT now(),
 approved_at timestamptz,
 FOREIGN KEY(line_id,product_code) REFERENCES public.planning_product_settings(line_id,product_code)
);
CREATE INDEX planning_auto_runs_scope_date ON public.planning_auto_runs(line_id,product_code,start_date,created_at DESC);
ALTER TABLE public.planning_daily_capacity
 ADD COLUMN auto_run_id uuid REFERENCES public.planning_auto_runs(run_id),
 ADD COLUMN shift_code varchar(20),
 ADD COLUMN shift_label varchar(100),
 ADD COLUMN shift_hours numeric(5,2),
 ADD COLUMN is_locked boolean NOT NULL DEFAULT false;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.planning_auto_runs TO yigitcanc2;
COMMIT;
SELECT has_table_privilege('yigitcanc2','public.planning_auto_runs','SELECT,INSERT,UPDATE,DELETE') AS auto_runs_rw;
