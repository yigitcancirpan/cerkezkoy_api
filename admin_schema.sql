CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO system_settings (key, value) VALUES
  ('oee_target', '67')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS press_oil_thresholds (
    press_id   INT PRIMARY KEY,
    level_min  REAL, level_max REAL,
    temp_min   REAL, temp_max  REAL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO press_oil_thresholds (press_id, level_min, level_max, temp_min, temp_max) VALUES
  (1, 20, 95, 10, 60),
  (2, 20, 95, 10, 60),
  (3, 20, 95, 10, 60)
ON CONFLICT (press_id) DO NOTHING;