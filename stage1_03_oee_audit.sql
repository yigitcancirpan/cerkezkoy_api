-- Aşama 1.03 — OEE öncesi salt okunur veri denetimi
-- Hiçbir tabloyu değiştirmez.

\pset pager off
\set ON_ERROR_STOP on

SELECT now() AS database_now, current_setting('TimeZone') AS timezone;

SELECT line_id, line_name, is_active, ideal_cycle_ds,
       ideal_cycle_ds / 10.0 AS ideal_cycle_sec
FROM public.production_lines
ORDER BY line_id;

-- Ürün/model çevrimi performansın ana kaynağıdır. Aynı model_id için farklı
-- değer çıkarsa migration uygulanmadan veri düzeltilmelidir.
SELECT material_id, material_code, material_name, model_id,
       ideal_cycle_ds, ideal_cycle_ds / 10.0 AS ideal_cycle_sec,
       is_active
FROM public.materials
ORDER BY model_id NULLS LAST, material_code;

SELECT model_id,
       ARRAY_AGG(DISTINCT ideal_cycle_ds ORDER BY ideal_cycle_ds) AS cycles_ds,
       COUNT(DISTINCT ideal_cycle_ds) AS distinct_cycle_count
FROM public.materials
WHERE model_id IS NOT NULL AND ideal_cycle_ds IS NOT NULL
GROUP BY model_id
HAVING COUNT(DISTINCT ideal_cycle_ds) > 1
ORDER BY model_id;

SELECT DISTINCT model_id
FROM public.production_log
WHERE model_id IS NOT NULL
EXCEPT
SELECT DISTINCT model_id
FROM public.materials
WHERE model_id IS NOT NULL AND ideal_cycle_ds IS NOT NULL;

SELECT reason_id, reason_code, reason_name, category,
       exclude_from_oee, is_active
FROM public.downtime_reasons
ORDER BY display_order, reason_id;

SELECT reason_code, category, exclude_from_oee, is_active
FROM public.downtime_reasons
WHERE reason_code IN ('OPERATOR', 'BELIRLENMEDI', 'VARDIYA_SONU')
ORDER BY reason_code;

SELECT code, label, start_hour, end_hour, latest_end,
       planned_seconds, line_id, day_of_week, is_active
FROM public.shift_config
ORDER BY day_of_week NULLS FIRST, line_id NULLS FIRST, display_order;

SELECT override_date, code, line_id, start_hour, end_hour,
       latest_end, planned_seconds, note
FROM public.shift_overrides
ORDER BY override_date, code, line_id NULLS FIRST;

SELECT line_id,
       MIN(logged_at) AS first_log,
       MAX(logged_at) AS last_log,
       COUNT(*) AS log_count
FROM public.production_log
GROUP BY line_id
ORDER BY line_id;

WITH ordered AS (
    SELECT line_id, logged_at::date AS log_date, shift, logged_at,
           produced,
           LAG(produced) OVER (
               PARTITION BY line_id, logged_at::date, shift
               ORDER BY logged_at, log_id
           ) AS previous_produced
    FROM public.production_log
)
SELECT line_id, log_date, shift,
       COUNT(*) FILTER (
           WHERE previous_produced IS NOT NULL
             AND produced < previous_produced
       ) AS counter_decrease_count
FROM ordered
GROUP BY line_id, log_date, shift
HAVING COUNT(*) FILTER (
           WHERE previous_produced IS NOT NULL
             AND produced < previous_produced
       ) > 0
ORDER BY log_date, line_id, shift;

SELECT line_id,
       MIN(shift_date) AS first_summary,
       MAX(shift_date) AS last_summary,
       COUNT(*) AS summary_count,
       ROUND(AVG(oee_overall)::numeric, 1) AS simple_avg_oee
FROM public.shift_summary
GROUP BY line_id
ORDER BY line_id;

SELECT summary_id, line_id, shift_date, shift,
       total_produced, total_scrap, total_good,
       total_downtime_sec, break_sec,
       oee_availability, oee_performance, oee_quality, oee_overall,
       created_at
FROM public.shift_summary
ORDER BY shift_date DESC, line_id, shift
LIMIT 30;

SELECT downtime_id, line_id, reason_id, shift, started_at
FROM public.downtimes
WHERE is_active=TRUE
ORDER BY line_id;
