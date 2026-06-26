CREATE TABLE production_lines (
    line_id         SERIAL PRIMARY KEY,
    line_name       VARCHAR(100) NOT NULL,
    location        VARCHAR(50) NOT NULL,
    description     VARCHAR(500),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE machines (
    machine_id      SERIAL PRIMARY KEY,
    line_id         INT NOT NULL REFERENCES production_lines(line_id),
    machine_name    VARCHAR(100) NOT NULL,
    machine_type    VARCHAR(50),
    manufacturer    VARCHAR(100),
    model           VARCHAR(100),
    serial_number   VARCHAR(100),
    install_date    DATE,
    status          VARCHAR(20) DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ix_machines_line ON machines(line_id);
CREATE INDEX ix_machines_status ON machines(status);

CREATE TABLE sensors (
    sensor_id       SERIAL PRIMARY KEY,
    machine_id      INT NOT NULL REFERENCES machines(machine_id),
    sensor_name     VARCHAR(100) NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    unit            VARCHAR(20),
    min_threshold   FLOAT,
    max_threshold   FLOAT,
    reading_interval INT DEFAULT 5,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ix_sensors_machine ON sensors(machine_id);
CREATE INDEX ix_sensors_type ON sensors(sensor_type);

CREATE TABLE sensor_readings (
    reading_id      BIGSERIAL,
    sensor_id       INT NOT NULL,
    machine_id      INT NOT NULL,
    line_id         INT NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    quality         INT DEFAULT 100,
    device_id       VARCHAR(50),
    reading_time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id        VARCHAR(50),
    PRIMARY KEY (reading_time, reading_id)
) PARTITION BY RANGE (reading_time);

-- Aylık partition'lar (gerektiğinde yeni ay ekle)
CREATE TABLE sensor_readings_2026_03 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE sensor_readings_2026_04 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE sensor_readings_2026_05 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE sensor_readings_2026_06 PARTITION OF sensor_readings
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX ix_readings_sensor ON sensor_readings(sensor_id, reading_time DESC);
CREATE INDEX ix_readings_machine ON sensor_readings(machine_id, reading_time DESC);
CREATE INDEX ix_readings_type ON sensor_readings(sensor_type, reading_time DESC);

CREATE TABLE sensor_hourly_summary (
    summary_id      BIGSERIAL PRIMARY KEY,
    sensor_id       INT NOT NULL,
    machine_id      INT NOT NULL,
    line_id         INT NOT NULL,
    sensor_type     VARCHAR(50) NOT NULL,
    hour_bucket     TIMESTAMPTZ NOT NULL,
    avg_value       FLOAT,
    max_value       FLOAT,
    min_value       FLOAT,
    stddev_value    FLOAT,
    reading_count   INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX ix_hourly_unique ON sensor_hourly_summary(sensor_id, hour_bucket);

CREATE TABLE alerts (
    alert_id        BIGSERIAL PRIMARY KEY,
    sensor_id       INT NOT NULL REFERENCES sensors(sensor_id),
    machine_id      INT NOT NULL,
    alert_type      VARCHAR(20) NOT NULL,
    alert_message   VARCHAR(500),
    trigger_value   FLOAT,
    threshold_value FLOAT,
    is_acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,
    triggered_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX ix_alerts_active ON alerts(is_acknowledged, triggered_at DESC);

CREATE TABLE batch_transfers (
    batch_id        VARCHAR(50) PRIMARY KEY,
    source_device   VARCHAR(50),
    record_count    INT,
    start_time      TIMESTAMPTZ,
    end_time        TIMESTAMPTZ,
    transferred_at  TIMESTAMPTZ DEFAULT NOW(),
    status          VARCHAR(20) DEFAULT 'pending',
    error_message   TEXT
);

CREATE OR REPLACE FUNCTION generate_hourly_summary(
    p_start_time TIMESTAMPTZ DEFAULT NULL,
    p_end_time TIMESTAMPTZ DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    IF p_start_time IS NULL THEN p_start_time := NOW() - INTERVAL '2 hours'; END IF;
    IF p_end_time IS NULL THEN p_end_time := NOW(); END IF;

    INSERT INTO sensor_hourly_summary
        (sensor_id, machine_id, line_id, sensor_type, hour_bucket,
         avg_value, max_value, min_value, stddev_value, reading_count)
    SELECT sensor_id, machine_id, line_id, sensor_type,
           date_trunc('hour', reading_time) AS hour_bucket,
           AVG(value), MAX(value), MIN(value), STDDEV(value), COUNT(*)
    FROM sensor_readings
    WHERE reading_time >= p_start_time AND reading_time < p_end_time
    GROUP BY sensor_id, machine_id, line_id, sensor_type, date_trunc('hour', reading_time)
    ON CONFLICT (sensor_id, hour_bucket)
    DO UPDATE SET avg_value=EXCLUDED.avg_value, max_value=EXCLUDED.max_value,
                  min_value=EXCLUDED.min_value, stddev_value=EXCLUDED.stddev_value,
                  reading_count=EXCLUDED.reading_count;
END;
$$ LANGUAGE plpgsql;