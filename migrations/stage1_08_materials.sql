\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='120s';
LOCK TABLE materials IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF current_database() <> 'cerkezkoy_db' THEN RAISE EXCEPTION 'Yanlış veritabanı'; END IF;
 IF EXISTS (SELECT 1 FROM materials WHERE model_id>0 GROUP BY model_id HAVING COUNT(*)>1)
 THEN RAISE EXCEPTION 'Tekrarlanan model numarası var; otomatik eşleme durduruldu'; END IF;
END $$;
CREATE SEQUENCE IF NOT EXISTS public.material_model_seq;
SELECT setval('public.material_model_seq', GREATEST(
 COALESCE((SELECT MAX(model_id) FROM materials),0),
 COALESCE((SELECT MAX(model_id) FROM production_log),0),
 COALESCE((SELECT MAX(model_id) FROM production_current),0),
 COALESCE((SELECT MAX(model_id) FROM shift_summary),0),
 (SELECT last_value FROM public.material_model_seq)), true);
UPDATE materials SET model_id=nextval('public.material_model_seq'), updated_at=NOW()
 WHERE model_id IS NULL OR model_id<=0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_material_model_identity ON materials(model_id) WHERE model_id>0;
CREATE OR REPLACE FUNCTION public.guard_material_model() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.model_id>0 AND NEW.model_id IS DISTINCT FROM OLD.model_id THEN
  RAISE EXCEPTION 'Model kimliği geçmiş üretimi korumak için değiştirilemez';
 END IF;
 IF NEW.model_id IS NULL OR NEW.model_id<=0 THEN
  LOOP
   NEW.model_id := nextval('public.material_model_seq');
   EXIT WHEN NOT EXISTS(SELECT 1 FROM public.materials WHERE model_id=NEW.model_id);
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS material_model_guard ON materials;
CREATE TRIGGER material_model_guard BEFORE INSERT OR UPDATE ON materials
 FOR EACH ROW EXECUTE FUNCTION public.guard_material_model();
GRANT USAGE, SELECT ON SEQUENCE public.material_model_seq TO yigitcanc2;

ALTER TABLE scrap_entries ADD COLUMN IF NOT EXISTS material_id integer REFERENCES materials(material_id);
CREATE INDEX IF NOT EXISTS ix_scrap_material_date ON scrap_entries(line_id,material_id,production_date);
-- Only exact single-model summaries can identify historical scrap. Never infer model 0.
WITH eligible AS (
 SELECT s.line_id,s.shift_date,s.shift,MIN(m.material_id) AS material_id
 FROM shift_summary s
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.ideal_cycle_detail,'[]'::jsonb)) d
 LEFT JOIN materials m ON m.model_id=(d->>'model_id')::integer
 WHERE COALESCE((d->>'produced')::numeric,0)>0
 GROUP BY s.line_id,s.shift_date,s.shift
 HAVING COUNT(DISTINCT m.material_id)=1 AND COUNT(*)=COUNT(m.material_id)
 AND SUM((d->>'produced')::numeric)=MAX(s.total_produced)
)
UPDATE scrap_entries e SET material_id=x.material_id FROM eligible x
 WHERE e.material_id IS NULL AND e.line_id=x.line_id
 AND e.production_date=x.shift_date AND e.shift=x.shift;

INSERT INTO planning_product_settings(line_id,product_code,workdays)
 SELECT 2,material_code,'[]'::jsonb FROM materials
 WHERE material_code IN ('2962070700','2962070100') AND is_active
 ON CONFLICT(line_id,product_code) DO NOTHING;
COMMIT;
SELECT material_id,material_code,model_id FROM materials ORDER BY material_id;
SELECT COUNT(*) AS unallocated_scrap_entries FROM scrap_entries WHERE material_id IS NULL;
