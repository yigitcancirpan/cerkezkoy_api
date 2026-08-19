-- Çerkezköy API - Aşama 0.1
-- Amaç: Mevcut yigitcanc2 rolüne, sahiplik vermeden uygulamanın çalışma
-- zamanı için gereken yetkileri tanımlamak.
--
-- Bu dosya:
--   * Veri eklemez, değiştirmez veya silmez.
--   * Tablo/şema/veritabanı sahipliğini değiştirmez.
--   * Mevcut yigitcanc rolünü değiştirmez.
--   * sensor_readings partition yapısını değiştirmez.
--   * Tek transaction içinde çalışır; hata olursa tamamı geri alınır.
--
-- Çalıştırma:
--   sudo -u postgres psql -X -v ON_ERROR_STOP=1 \
--     -d cerkezkoy_db \
--     -f stage0_01_grant_runtime_role.sql

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Yanlış sunucu/veritabanında çalıştırılmasını engelle.
DO $guard$
BEGIN
    IF current_database() <> 'cerkezkoy_db' THEN
        RAISE EXCEPTION
            'Yanlış veritabanı: %, beklenen: cerkezkoy_db',
            current_database();
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'yigitcanc2'
          AND rolcanlogin
    ) THEN
        RAISE EXCEPTION
            'LOGIN yetkili yigitcanc2 rolü bulunamadı';
    END IF;
END
$guard$;

-- Veritabanına bağlanma ve public şemasındaki nesnelere erişim.
GRANT CONNECT ON DATABASE cerkezkoy_db TO yigitcanc2;
GRANT USAGE ON SCHEMA public TO yigitcanc2;

-- API; okuma yanında üretim, duruş, hurda, atama ve ayar kayıtlarında
-- INSERT/UPDATE/DELETE işlemleri gerçekleştiriyor.
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO yigitcanc2;

-- SERIAL/BIGSERIAL kolonlarının nextval/currval işlemleri.
GRANT USAGE, SELECT
ON ALL SEQUENCES IN SCHEMA public
TO yigitcanc2;

-- public.generate_hourly_summary dahil mevcut fonksiyonlar.
GRANT EXECUTE
ON ALL FUNCTIONS IN SCHEMA public
TO yigitcanc2;

-- Mevcut nesnelerin sahibi yigitcanc olduğundan, onun ileride oluşturacağı
-- tablo/partition/sequence/fonksiyonlara aynı çalışma zamanı yetkilerini ver.
ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO yigitcanc2;

ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
GRANT USAGE, SELECT ON SEQUENCES TO yigitcanc2;

ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
GRANT EXECUTE ON FUNCTIONS TO yigitcanc2;

COMMIT;

-- -------------------------------------------------------------------------
-- DOĞRULAMA - salt okunur
-- -------------------------------------------------------------------------

SELECT
    current_database() AS database_name,
    has_database_privilege('yigitcanc2', current_database(), 'CONNECT')
        AS can_connect,
    has_schema_privilege('yigitcanc2', 'public', 'USAGE')
        AS can_use_public_schema,
    has_schema_privilege('yigitcanc2', 'public', 'CREATE')
        AS can_create_in_public_schema;

SELECT
    COUNT(*) FILTER (
        WHERE has_table_privilege(
            'yigitcanc2', format('%I.%I', schemaname, tablename), 'SELECT'
        )
    ) AS select_allowed,
    COUNT(*) FILTER (
        WHERE has_table_privilege(
            'yigitcanc2', format('%I.%I', schemaname, tablename), 'INSERT'
        )
    ) AS insert_allowed,
    COUNT(*) FILTER (
        WHERE has_table_privilege(
            'yigitcanc2', format('%I.%I', schemaname, tablename), 'UPDATE'
        )
    ) AS update_allowed,
    COUNT(*) FILTER (
        WHERE has_table_privilege(
            'yigitcanc2', format('%I.%I', schemaname, tablename), 'DELETE'
        )
    ) AS delete_allowed,
    COUNT(*) AS application_tables
FROM pg_tables
WHERE schemaname = 'public';

SELECT
    COUNT(*) FILTER (
        WHERE has_sequence_privilege(
            'yigitcanc2', format('%I.%I', sequence_schema, sequence_name),
            'USAGE'
        )
    ) AS sequence_usage_allowed,
    COUNT(*) AS application_sequences
FROM information_schema.sequences
WHERE sequence_schema = 'public';

SELECT
    p.oid::regprocedure AS function_name,
    has_function_privilege('yigitcanc2', p.oid, 'EXECUTE') AS can_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
ORDER BY p.oid::regprocedure::text;

-- Beklenen önemli sonuçlar:
--   can_connect              = t
--   can_use_public_schema    = t
--   can_create_in_public_schema = f
--   Tablo sayaçlarının tamamı application_tables ile aynı
--   sequence_usage_allowed = application_sequences
--
-- -------------------------------------------------------------------------
-- GERİ ALMA - yalnızca ihtiyaç halinde, yorum işaretleri kaldırılarak çalışır
-- -------------------------------------------------------------------------
-- BEGIN;
-- ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
--     REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM yigitcanc2;
-- ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
--     REVOKE USAGE, SELECT ON SEQUENCES FROM yigitcanc2;
-- ALTER DEFAULT PRIVILEGES FOR ROLE yigitcanc IN SCHEMA public
--     REVOKE EXECUTE ON FUNCTIONS FROM yigitcanc2;
-- REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM yigitcanc2;
-- REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM yigitcanc2;
-- REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM yigitcanc2;
-- REVOKE USAGE ON SCHEMA public FROM yigitcanc2;
-- REVOKE CONNECT ON DATABASE cerkezkoy_db FROM yigitcanc2;
-- COMMIT;
