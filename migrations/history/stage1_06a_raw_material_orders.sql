-- Çerkezköy API - Aşama 1.06a
-- Hammadde stok bilgisinden bağımsız, çoklu parti siparişleri.
-- Önce stage1_06_planning.sql uygulanmış olmalıdır.
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $guard$
BEGIN
    IF current_database() <> 'cerkezkoy_db' THEN
        RAISE EXCEPTION 'Yanlış veritabanı: %', current_database();
    END IF;
    IF to_regclass('public.planning_product_settings') IS NULL THEN
        RAISE EXCEPTION 'Önce stage1_06_planning.sql uygulanmalı';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'yigitcanc2' AND rolcanlogin
    ) THEN
        RAISE EXCEPTION 'Runtime rolü yigitcanc2 bulunamadı';
    END IF;
END
$guard$;

CREATE TABLE IF NOT EXISTS public.planning_raw_orders (
    raw_order_id bigserial PRIMARY KEY,
    line_id integer NOT NULL,
    product_code varchar(50) NOT NULL,
    expected_date date NOT NULL,
    order_qty numeric(16,3) NOT NULL CHECK (order_qty > 0),
    batch_ref varchar(100),
    status varchar(20) NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'confirmed', 'received', 'cancelled')),
    notes varchar(300),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT planning_raw_orders_settings_fk
        FOREIGN KEY (line_id, product_code)
        REFERENCES public.planning_product_settings(line_id, product_code)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_planning_raw_orders_expected
    ON public.planning_raw_orders(expected_date, line_id, product_code);

GRANT SELECT, INSERT, UPDATE, DELETE
ON public.planning_raw_orders
TO yigitcanc2;

GRANT USAGE, SELECT
ON SEQUENCE public.planning_raw_orders_raw_order_id_seq
TO yigitcanc2;

COMMIT;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'planning_raw_orders';

SELECT
    has_table_privilege(
        'yigitcanc2', 'public.planning_raw_orders',
        'SELECT,INSERT,UPDATE,DELETE'
    ) AS raw_orders_rw,
    has_sequence_privilege(
        'yigitcanc2', 'public.planning_raw_orders_raw_order_id_seq',
        'USAGE,SELECT'
    ) AS raw_orders_sequence;
