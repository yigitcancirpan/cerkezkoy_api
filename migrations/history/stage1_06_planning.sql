-- Çerkezköy API - Aşama 1.06
-- 2962070700 / World Hattı için bağımsız üretim planlama tabloları.
-- Mevcut üretim, OEE, duruş ve fire tablolarını değiştirmez.

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $guard$
BEGIN
    IF current_database() <> 'cerkezkoy_db' THEN
        RAISE EXCEPTION 'Yanlış veritabanı: %', current_database();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.production_lines
        WHERE line_id = 2 AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'World Hattı (line_id=2) bulunamadı';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.materials
        WHERE material_code = '2962070700' AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'Aktif 2962070700 malzeme kaydı bulunamadı';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'yigitcanc2' AND rolcanlogin
    ) THEN
        RAISE EXCEPTION 'Runtime rolü yigitcanc2 bulunamadı';
    END IF;
END
$guard$;

CREATE TABLE IF NOT EXISTS public.planning_product_settings (
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    finished_stock integer NOT NULL DEFAULT 0 CHECK (finished_stock >= 0),
    finished_stock_as_of date NOT NULL DEFAULT CURRENT_DATE,
    finished_safety_stock integer NOT NULL DEFAULT 0
        CHECK (finished_safety_stock >= 0),
    raw_material_code varchar(80),
    raw_material_name varchar(150),
    raw_unit varchar(20) NOT NULL DEFAULT 'kg',
    raw_stock_qty numeric(16,3) NOT NULL DEFAULT 0 CHECK (raw_stock_qty >= 0),
    raw_stock_as_of date NOT NULL DEFAULT CURRENT_DATE,
    raw_per_piece numeric(16,6) NOT NULL DEFAULT 0 CHECK (raw_per_piece >= 0),
    raw_scrap_pct numeric(7,3) NOT NULL DEFAULT 0
        CHECK (raw_scrap_pct >= 0 AND raw_scrap_pct <= 100),
    raw_safety_stock numeric(16,3) NOT NULL DEFAULT 0
        CHECK (raw_safety_stock >= 0),
    workdays jsonb NOT NULL DEFAULT '[0,1,2,3,4,5]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_id, product_code),
    CONSTRAINT planning_product_settings_line_fk
        FOREIGN KEY (line_id) REFERENCES public.production_lines(line_id),
    CONSTRAINT planning_product_settings_workdays_array
        CHECK (jsonb_typeof(workdays) = 'array')
);

CREATE TABLE IF NOT EXISTS public.planning_orders (
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    order_date date NOT NULL,
    order_qty integer NOT NULL CHECK (order_qty > 0),
    notes varchar(300),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_id, product_code, order_date),
    CONSTRAINT planning_orders_settings_fk
        FOREIGN KEY (line_id, product_code)
        REFERENCES public.planning_product_settings(line_id, product_code)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS public.planning_weekly_capacity (
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    week_start date NOT NULL,
    daily_default_qty integer NOT NULL CHECK (daily_default_qty >= 0),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_id, product_code, week_start),
    CONSTRAINT planning_weekly_settings_fk
        FOREIGN KEY (line_id, product_code)
        REFERENCES public.planning_product_settings(line_id, product_code)
        ON DELETE RESTRICT,
    CONSTRAINT planning_week_starts_monday
        CHECK (EXTRACT(ISODOW FROM week_start) = 1)
);

CREATE TABLE IF NOT EXISTS public.planning_daily_capacity (
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    plan_date date NOT NULL,
    planned_qty integer NOT NULL CHECK (planned_qty >= 0),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_id, product_code, plan_date),
    CONSTRAINT planning_daily_settings_fk
        FOREIGN KEY (line_id, product_code)
        REFERENCES public.planning_product_settings(line_id, product_code)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS public.planning_raw_receipts (
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    receipt_date date NOT NULL,
    receipt_qty numeric(16,3) NOT NULL CHECK (receipt_qty > 0),
    notes varchar(300),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (line_id, product_code, receipt_date),
    CONSTRAINT planning_raw_settings_fk
        FOREIGN KEY (line_id, product_code)
        REFERENCES public.planning_product_settings(line_id, product_code)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_planning_orders_date
    ON public.planning_orders(order_date, line_id);
CREATE INDEX IF NOT EXISTS idx_planning_daily_date
    ON public.planning_daily_capacity(plan_date, line_id);
CREATE INDEX IF NOT EXISTS idx_planning_receipts_date
    ON public.planning_raw_receipts(receipt_date, line_id);

INSERT INTO public.planning_product_settings
    (line_id, product_code, finished_stock_as_of, raw_stock_as_of)
VALUES (2, '2962070700', CURRENT_DATE, CURRENT_DATE)
ON CONFLICT (line_id, product_code) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE
ON public.planning_product_settings,
   public.planning_orders,
   public.planning_weekly_capacity,
   public.planning_daily_capacity,
   public.planning_raw_receipts
TO yigitcanc2;

COMMIT;

-- Salt okunur doğrulama
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'planning_%'
ORDER BY table_name;

SELECT line_id, product_code, finished_stock, raw_stock_qty,
       raw_per_piece, raw_scrap_pct, workdays
FROM public.planning_product_settings
WHERE line_id = 2 AND product_code = '2962070700';

SELECT
    has_table_privilege('yigitcanc2',
        'public.planning_product_settings', 'SELECT,INSERT,UPDATE,DELETE')
        AS settings_rw,
    has_table_privilege('yigitcanc2',
        'public.planning_orders', 'SELECT,INSERT,UPDATE,DELETE')
        AS orders_rw,
    has_table_privilege('yigitcanc2',
        'public.planning_weekly_capacity', 'SELECT,INSERT,UPDATE,DELETE')
        AS weekly_rw,
    has_table_privilege('yigitcanc2',
        'public.planning_daily_capacity', 'SELECT,INSERT,UPDATE,DELETE')
        AS daily_rw,
    has_table_privilege('yigitcanc2',
        'public.planning_raw_receipts', 'SELECT,INSERT,UPDATE,DELETE')
        AS receipts_rw;
