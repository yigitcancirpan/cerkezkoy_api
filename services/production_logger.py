"""
Üretim Veri Kaydedici — v3 (Zamanlanmış Vardiya Özeti)
services/production_logger.py

Vardiya özeti:
  - Sinyal BEKLEMİYOR
  - Her vardiyarın latest_end saatinde otomatik hesaplar
  - Örnek: Vardiya 1 latest_end=19 → saat 19:00'da özet yazılır
  - Her gün otomatik çalışır, müdahale gerektirmez

MQTT:
  fabrika/hat1/uretim     → üretim verileri
  fabrika/hat1/cycle_time → cycle süresi
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("production_logger")


DEFAULT_SHIFTS = [
    {
        "code": "vardiya_1", "label": "1. Vardiya",
        "start_hour": 8, "end_hour": 18,
        "earliest_end": 17, "latest_end": 19,
    },
    {
        "code": "vardiya_2", "label": "2. Vardiya",
        "start_hour": 18, "end_hour": 24,
        "earliest_end": 23, "latest_end": 1,
    },
]


class ShiftDetector:
    def __init__(self, shifts=None):
        self.shifts = shifts or DEFAULT_SHIFTS

    def current_shift(self):
        h = datetime.now().hour
        for s in self.shifts:
            start, latest = s["start_hour"], s["latest_end"]
            if start > latest:
                if h >= start or h < latest: return s
            else:
                if start <= h < latest: return s
        return self.shifts[0]

    def current_code(self): return self.current_shift()["code"]
    def current_label(self): return self.current_shift()["label"]


class LineState:
    def __init__(self, line_id):
        self.line_id = line_id
        self.lot_number = 0; self.model_id = 0
        self.target = 0; self.produced = 0; self.scrap = 0; self.good = 0
        self.actual_cycle = 0; self.average_cycle = 0; self.last_5 = []
        self.last_activity_time = time.time()
        self.last_logged_produced = -1
        self.first_cycle_at = None
        self.last_cycle_at = None
        self.shift_start_produced = 0
        self.current_shift_code = None
        self.shift_key = None
        self.cycle_running: Optional[bool] = None
        self.auto_cycle_on: Optional[bool] = None 

class OilState:
    """Her pres için son okuma + son DB'ye yazma takibi"""
    def __init__(self, press_id):
        self.press_id = press_id
        self.last_level = None
        self.last_temp = None
        self.last_db_write = 0.0  # time.time()
        self.last_db_level = None
        self.last_db_temp = None

    
class ProductionLogger:
     # Eşikler
    OIL_LEVEL_THRESHOLD = 0.5      # %
    OIL_TEMP_THRESHOLD = 0.5       # °C
    OIL_HEARTBEAT_SEC = 600        # 10 dakika
    def __init__(self, db_url="", mqtt_host="127.0.0.1", mqtt_port=1883,
                 log_interval_sec=60, shifts_config=None, lines_config=None):
        self.db_url = db_url
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.log_interval = log_interval_sec

        self.shift_detector = ShiftDetector(shifts_config)
        lines_config = lines_config or [{"line_id": 1, "mqtt_prefix": "fabrika/hat1"}]

        self.prefix_to_line = {}
        self.lines = {}
        self.oil_states = {pid: OilState(pid) for pid in range(1, 4)}
        for c in lines_config:
            lid = c["line_id"]
            self.prefix_to_line[c["mqtt_prefix"]] = lid
            self.lines[lid] = LineState(lid)

        self.client = mqtt.Client(client_id="production_logger", protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._running = False
        self._db_conn = None

        # Hangi vardiya özetleri bugün yazıldı
        self._summaries_written = set()  # "2026-05-12_vardiya_1" gibi

    def _get_db(self):
        if self._db_conn is None or self._db_conn.closed:
            self._db_conn = psycopg2.connect(self.db_url)
            self._db_conn.autocommit = True
        return self._db_conn

    # ─── DB İŞLEMLERİ ─────────────────────────
    def _update_current(self, line_id):
        s = self.lines[line_id]
        try:
            db = self._get_db()
            cur = db.cursor()
            cur.execute("""
                INSERT INTO production_current
                    (line_id, lot_number, model_id, target, produced, scrap, good,
                     actual_cycle, average_cycle, last_5_cycles,
                     first_cycle_at, last_cycle_at, shift_start_produced, current_shift,
                     updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (line_id) DO UPDATE SET
                    lot_number=EXCLUDED.lot_number, model_id=EXCLUDED.model_id,
                    target=EXCLUDED.target, produced=EXCLUDED.produced,
                    scrap=EXCLUDED.scrap, good=EXCLUDED.good,
                    actual_cycle=EXCLUDED.actual_cycle, average_cycle=EXCLUDED.average_cycle,
                    last_5_cycles=EXCLUDED.last_5_cycles,
                    first_cycle_at=COALESCE(production_current.first_cycle_at, EXCLUDED.first_cycle_at),
                    last_cycle_at=EXCLUDED.last_cycle_at,
                    shift_start_produced=EXCLUDED.shift_start_produced,
                    current_shift=EXCLUDED.current_shift,
                    updated_at=NOW()
            """, (line_id, s.lot_number, s.model_id, s.target, s.produced,
                  s.scrap, s.good, s.actual_cycle, s.average_cycle, json.dumps(s.last_5),
                  s.first_cycle_at, s.last_cycle_at, s.shift_start_produced,
                  s.current_shift_code))
            cur.close()
        except Exception as e:
            logger.error(f"current update: {e}"); self._db_conn = None

    def _write_log(self, line_id):
        s = self.lines[line_id]
        try:
            db = self._get_db()
            cur = db.cursor()
            cur.execute("""
                INSERT INTO production_log
                    (line_id, lot_number, model_id, target, produced, scrap, good,
                     actual_cycle, average_cycle, shift, logged_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """, (line_id, s.lot_number, s.model_id, s.target, s.produced,
                  s.scrap, s.good, s.actual_cycle, s.average_cycle,
                  self.shift_detector.current_code()))
            cur.close()
        except Exception as e:
            logger.error(f"log write: {e}"); self._db_conn = None

    def _write_shift_summary(self, line_id, shift_code, shift_date=None):
        """Vardiya özeti hesapla ve yaz — MIN/MAX farkı ile gerçek vardiya üretimi"""
        try:
            db = self._get_db()
            cur = db.cursor(cursor_factory=RealDictCursor)
            d = shift_date or date.today()

            # ⚠️ KRİTİK: shift_produced = vardiya boyunca SAYILAN üretim
            # (max - min), MAX değil. Aksi takdirde önceki vardiyaların birikmiş
            # sayacı bu vardiyaya saçma rakamlar ekler.
            cur.execute("""
                SELECT MIN(logged_at) as first_at,
                    MAX(logged_at) as last_at,
                    MAX(produced) - MIN(produced) as shift_produced,
                    MAX(scrap) - MIN(scrap) as shift_scrap,
                    MAX(target) as target,
                    MAX(model_id) as model_id,
                    AVG(average_cycle) as avg_cycle
                FROM production_log
                WHERE line_id=%s AND shift=%s AND logged_at::date=%s
            """, (line_id, shift_code, d))
            row = cur.fetchone()

            if not row or not row["shift_produced"] or row["shift_produced"] == 0:
                logger.info(f"[Hat {line_id}] {shift_code} {d} — üretim verisi yok, özet atlanıyor")
                cur.close()
                return

            # Decimal → float/int (psycopg2 numeric tipleri Decimal döndürür)
            produced = int(row["shift_produced"] or 0)
            plc_scrap = int(row["shift_scrap"] or 0)   # PLC ham besleme sayacı — sadece referans
            target = int(row["target"] or 0)
            avg_cycle = float(row["avg_cycle"] or 0)
            first_at = row["first_at"]
            last_at = row["last_at"]

            # ── Gerçek fire: elle girilen scrap_entries (PLC scrap DEĞİL) ──
            cur.execute("""
                SELECT COALESCE(SUM(qty), 0) AS s
                FROM scrap_entries
                WHERE line_id=%s AND shift=%s AND created_at::date=%s
            """, (line_id, shift_code, d))
            scrap = int(cur.fetchone()["s"])
            good = max(produced - scrap, 0)

            # Duruş toplamı — VARDIYA_SONU hariç (gerçek üretkenlik kaybı değil)
            cur.execute("""
                SELECT COALESCE(SUM(d.duration_sec),0) as total_dt
                FROM downtimes d
                JOIN downtime_reasons r ON d.reason_id = r.reason_id
                WHERE d.line_id=%s AND d.shift=%s AND d.started_at::date=%s
                  AND d.is_active=FALSE
                  AND r.reason_code <> 'VARDIYA_SONU'
            """, (line_id, shift_code, d))
            dt_row = cur.fetchone()
            total_dt = int(dt_row["total_dt"]) if dt_row else 0

            # ── Planlanan üretim süresi = VARDİYANIN TAM SÜRESİ (vardiya_1 → 10 saat) ──
            # OEE HER ZAMAN planlanan süreye göre hesaplanır; ilk/son baskı penceresine DEĞİL.
            # Geç başlama / erken bitiş kaybı ancak böyle yakalanır.
            # first_at / last_at yalnızca first_cycle_at / last_cycle_at kolonlarında saklanır.
            shift_hours = 10
            for sd in self.shift_detector.shifts:
                if sd["code"] == shift_code:
                    shift_hours = sd["end_hour"] - sd["start_hour"]
                    if shift_hours <= 0:
                        shift_hours += 24
                    break
            total_time = shift_hours * 3600          # vardiya_1 → 36000 sn

            available = max(total_time - total_dt, 1)

            # OEE
            availability = min(available / total_time * 100, 100.0) if total_time > 0 else 0
            ideal_sec = (avg_cycle / 10.0) if avg_cycle > 0 else 8.0   # ⚠ desisaniye → saniye
            performance = (produced * ideal_sec / available * 100) if available > 0 else 0
            performance = min(performance, 100.0)
            quality = (good / produced * 100) if produced > 0 else 100
            oee = availability * performance * quality / 10000

            cur.execute("""
                INSERT INTO shift_summary
                    (line_id, shift_date, shift, model_id, target,
                    total_produced, total_scrap, total_good,
                    avg_cycle_time, total_downtime_sec,
                    oee_availability, oee_performance, oee_quality, oee_overall,
                    first_cycle_at, last_cycle_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (line_id, shift_date, shift) DO UPDATE SET
                    total_produced=EXCLUDED.total_produced, total_scrap=EXCLUDED.total_scrap,
                    total_good=EXCLUDED.total_good, avg_cycle_time=EXCLUDED.avg_cycle_time,
                    total_downtime_sec=EXCLUDED.total_downtime_sec,
                    oee_availability=EXCLUDED.oee_availability, oee_performance=EXCLUDED.oee_performance,
                    oee_quality=EXCLUDED.oee_quality, oee_overall=EXCLUDED.oee_overall,
                    first_cycle_at=EXCLUDED.first_cycle_at, last_cycle_at=EXCLUDED.last_cycle_at
            """, (line_id, d, shift_code, row["model_id"], target,
                produced, scrap, good, avg_cycle, total_dt,
                round(availability, 1), round(performance, 1),
                round(quality, 1), round(oee, 1),
                first_at, last_at))
            cur.close()

            logger.info(
                f"[Hat {line_id}] Vardiya özeti yazıldı: {shift_code} {d} | "
                f"Üretim:{produced} Fire:{scrap} Duruş:{total_dt//60}dk "
                f"İlk:{first_at.strftime('%H:%M') if first_at else '?'} "
                f"Son:{last_at.strftime('%H:%M') if last_at else '?'} "
                f"OEE:{oee:.1f}%"
            )
        except Exception as e:
            logger.error(f"shift summary: {e}"); self._db_conn = None

    # ─── MQTT ──────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("MQTT bağlantısı kuruldu")
            for prefix in self.prefix_to_line:
                client.subscribe(f"{prefix}/uretim")
                client.subscribe(f"{prefix}/cycle_time")
                client.subscribe(f"{prefix}/makine_durumu")
                client.subscribe(f"{prefix}/yag")  # ← YENİ
                logger.info(f"  Abone: {prefix}/makine_durumu, {prefix}/uretim, {prefix}/cycle_time")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            parts = msg.topic.rsplit("/", 1)
            if len(parts) != 2: return
            prefix, signal = parts
            line_id = self.prefix_to_line.get(prefix)
            if not line_id: return
            s = self.lines[line_id]

            if signal == "uretim":
                # Eski produced değerini sakla — baskı oldu mu tespiti için
                old_produced = s.produced
                new_produced = payload.get("produced", s.produced)
                produced_increased = new_produced > old_produced

                # Tüm değerleri güncelle
                s.lot_number = payload.get("lot_number", s.lot_number)
                s.model_id = payload.get("model_id", s.model_id)
                s.target = payload.get("target", s.target)
                s.produced = new_produced
                s.scrap = payload.get("scrap", s.scrap)
                s.good = payload.get("good", s.good)
                s.last_activity_time = time.time()

                # Vardiya/gün değişimi tespiti
                now = datetime.now()
                current_shift = self.shift_detector.current_code()
                today = now.strftime("%Y-%m-%d")
                shift_key = f"{today}_{current_shift}"

                if s.shift_key != shift_key:
                    s.shift_key = shift_key
                    s.current_shift_code = current_shift
                    s.first_cycle_at = None              # ← anlık saat DEĞİL, sıfırla
                    s.shift_start_produced = s.produced
                    try:
                        db = self._get_db()
                        cur = db.cursor()
                        cur.execute(
                            "UPDATE production_current SET first_cycle_at = NULL WHERE line_id = %s",
                            (line_id,)
                        )
                        cur.close()
                    except:
                        pass
                    logger.info(f"[Hat {line_id}] Yeni vardiya/gün: {shift_key}")

                s.current_shift_code = current_shift

                # SADECE üretim sayacı arttıysa baskı zamanını güncelle
                if produced_increased:
                    if s.first_cycle_at is None:
                        s.first_cycle_at = now
                    s.last_cycle_at = now

                self._update_current(line_id)

            elif signal == "cycle_time":
                s.actual_cycle = payload.get("actual", s.actual_cycle)
                s.average_cycle = payload.get("average", s.average_cycle)
                s.last_5 = payload.get("last_5", s.last_5)
                s.last_activity_time = time.time()
                self._update_current(line_id)
            elif signal == "makine_durumu":  # ← YENİ BLOK BAŞI
                new_running = bool(payload.get("production_state"))
                new_auto = bool(payload.get("auto_cycle_on"))

                if new_running != s.cycle_running or new_auto != getattr(s, "auto_cycle_on", None):
                    old_running = s.cycle_running
                    old_auto = getattr(s, "auto_cycle_on", None)
                    s.cycle_running = new_running
                    s.auto_cycle_on = new_auto
                    try:
                        db = self._get_db()
                        cur = db.cursor()
                        cur.execute("""
                            UPDATE production_current
                            SET cycle_running = %s,
                                auto_cycle_on = %s,
                                cycle_running_changed_at = %s
                            WHERE line_id = %s
                        """, (new_running, new_auto, datetime.now(), line_id))
                        cur.close()
                        logger.info(
                            f"[Hat {line_id}] durum: "
                            f"run={old_running}→{new_running}, auto={old_auto}→{new_auto}"
                        )
                    except Exception as e:
                        logger.error(f"durum update: {e}")
                        self._db_conn = None
            elif signal == "yag":
                self._handle_oil_data(payload)
        except json.JSONDecodeError: pass
        except Exception as e: logger.error(f"Mesaj: {e}")

    def _scheduler_loop(self):
        """
        Her 30 saniyede kontrol eder:
        - Bitiş saati geçmiş vardiyalar var mı?
        - shift_summary tablosunda kaydı yoksa → yaz
        DB-based idempotency: restart-safe, catch-up garantili.
        """
        # Servisin tam ayağa kalkmasını bekle
        time.sleep(10)
        
        # ── Başlangıçta catch-up: son 2 günün eksik vardiyalarını telafi et ──
        try:
            self._catch_up_missing_summaries(days_back=2)
        except Exception as e:
            logger.error(f"catch-up: {e}")
        
        while self._running:
            try:
                now = datetime.now()
                
                for shift in self.shift_detector.shifts:
                    shift_code = shift["code"]
                    
                    # Bu vardiyanın hangi tarihe ait olduğunu ve bitip bitmediğini hesapla
                    shift_date = self._resolve_shift_date(shift, now)
                    if shift_date is None:
                        continue  # Henüz bitmemiş
                    
                    # Her hat için DB'den kontrol et + gerekirse yaz
                    for line_id in self.lines:
                        if self._summary_exists(line_id, shift_date, shift_code):
                            continue
                        logger.info(
                            f"═══ Vardiya özeti tetiklendi: Hat {line_id} | "
                            f"{shift_code} | {shift_date} ═══"
                        )
                        self._write_shift_summary(line_id, shift_code, shift_date)
                        
            except Exception as e:
                logger.error(f"scheduler loop: {e}")
            
            time.sleep(30)


    def _resolve_shift_date(self, shift, now):
        """
        Vardiya şu an itibariyle bitmiş mi?
        Bittiyse hangi shift_date'e ait olduğunu döner, bitmediyse None.
        
        Örnek: vardiya_2 latest_end=1 (gece 01:00) ise:
        - Saat 02:00'da → bir önceki günün vardiya_2'si bitmiş
        - Saat 23:00'da → vardiya henüz bitmedi (None)
        """
        latest_end = shift["latest_end"]  # 0-23 arası int
        start_hour = shift["start_hour"]
        current_hour = now.hour
        
        # Vardiya gece yarısını geçiyor mu? (örn. 19→01)
        crosses_midnight = latest_end <= start_hour
        
        if not crosses_midnight:
            # Normal vardiya (örn. 07→19): bugün başlamış, bugün biter
            if current_hour >= latest_end:
                return now.date()
            return None
        else:
            # Gece vardiyası (örn. 19→01)
            if current_hour < latest_end:
                # Saat 00:30 gibi — dünkü vardiya yeni bitti
                return (now - timedelta(days=1)).date()
            elif current_hour >= start_hour:
                # Vardiya başladı ama henüz bitmedi
                return None
            else:
                # Saat 14:00 gibi — dünkü vardiya zaten çoktan bitti
                return (now - timedelta(days=1)).date()


    def _summary_exists(self, line_id, shift_date, shift_code):
        """shift_summary tablosunda bu kayıt var mı?"""
        try:
            db = self._get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT 1 FROM shift_summary
                WHERE line_id=%s AND shift_date=%s AND shift=%s
                LIMIT 1
            """, (line_id, shift_date, shift_code))
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        except Exception as e:
            logger.error(f"summary_exists: {e}")
            self._db_conn = None
            return False  # Şüpheli durumda yaz, INSERT ON CONFLICT zaten korur


    def _catch_up_missing_summaries(self, days_back=2):
        """
        Servis başlangıcında: son N günün eksik vardiya özetlerini yaz.
        Bugünün henüz bitmemiş vardiyaları için _resolve_shift_date None döner,
        o yüzden onlar zaten atlanır.
        """
        now = datetime.now()
        today = now.date()
        
        for day_offset in range(days_back, -1, -1):
            check_date = today - timedelta(days=day_offset)
            
            for shift in self.shift_detector.shifts:
                shift_code = shift["code"]
                
                # Bu gün için bu vardiya bitmiş mi?
                # Bugünse _resolve_shift_date'e bak; geçmiş günler için kesin bitmiş
                if check_date == today:
                    resolved = self._resolve_shift_date(shift, now)
                    if resolved != today:
                        continue  # Henüz bitmedi veya başka güne ait
                
                for line_id in self.lines:
                    if self._summary_exists(line_id, check_date, shift_code):
                        continue
                    logger.info(
                        f"[CATCH-UP] Eksik vardiya özeti yazılıyor: "
                        f"Hat {line_id} | {shift_code} | {check_date}"
                    )
                    self._write_shift_summary(line_id, shift_code, check_date)
    
    def _handle_oil_data(self, payload):
        """Yağ verisini DB'ye yaz — değişim eşiği + heartbeat ile"""
        now = time.time()
        
        try:
            db = self._get_db()
            cur = db.cursor()
            
            for press_id in range(1, 4):
                key = f"press{press_id}"
                press_data = payload.get(key)
                if not press_data:
                    continue
                
                level = press_data.get("level")
                temp = press_data.get("temp")
                if level is None or temp is None:
                    continue
                
                state = self.oil_states[press_id]
                
                # 1) press_oil_current'ı HER ZAMAN güncelle (anlık görünüm)
                cur.execute("""
                    INSERT INTO press_oil_current 
                        (press_id, level, temperature, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (press_id) DO UPDATE SET
                        level = EXCLUDED.level,
                        temperature = EXCLUDED.temperature,
                        updated_at = NOW()
                """, (press_id, level, temp))
                
                # 2) press_oil (geçmiş) — eşik/heartbeat kontrolü
                should_write = False
                
                # İlk kez geliyorsa kesin yaz
                if state.last_db_level is None or state.last_db_temp is None:
                    should_write = True
                    reason = "ilk-kayıt"
                # Heartbeat dolduysa yaz
                elif now - state.last_db_write >= self.OIL_HEARTBEAT_SEC:
                    should_write = True
                    reason = "heartbeat"
                # Seviye eşiği aşıldıysa
                elif abs(level - state.last_db_level) >= self.OIL_LEVEL_THRESHOLD:
                    should_write = True
                    reason = f"Δseviye={level-state.last_db_level:+.2f}"
                # Sıcaklık eşiği aşıldıysa
                elif abs(temp - state.last_db_temp) >= self.OIL_TEMP_THRESHOLD:
                    should_write = True
                    reason = f"Δsıcaklık={temp-state.last_db_temp:+.2f}"
                
                if should_write:
                    cur.execute("""
                        INSERT INTO press_oil (press_id, level, temperature, recorded_at)
                        VALUES (%s, %s, %s, NOW())
                    """, (press_id, level, temp))
                    state.last_db_write = now
                    state.last_db_level = level
                    state.last_db_temp = temp
                    logger.info(
                        f"[Press{press_id}] yağ kaydedildi: "
                        f"seviye={level}% sıcaklık={temp}°C ({reason})"
                    )
                
                state.last_level = level
                state.last_temp = temp
            
            # 24 saatlik min/max güncellemesi (her okumada yapma, 10dk'da bir yeterli)
            if int(now) % 600 < 60:
                cur.execute("""
                    UPDATE press_oil_current pc SET
                        level_min_24h = stats.lmin,
                        level_max_24h = stats.lmax,
                        temp_min_24h  = stats.tmin,
                        temp_max_24h  = stats.tmax
                    FROM (
                        SELECT press_id,
                            MIN(level) lmin, MAX(level) lmax,
                            MIN(temperature) tmin, MAX(temperature) tmax
                        FROM press_oil
                        WHERE recorded_at > NOW() - INTERVAL '24 hours'
                        GROUP BY press_id
                    ) stats
                    WHERE pc.press_id = stats.press_id
                """)
            
            cur.close()
        except Exception as e:
            logger.error(f"yağ DB: {e}")
            self._db_conn = None


   
    # ─── LOG DÖNGÜSÜ ──────────────────────────
    def _log_loop(self):
        while self._running:
            time.sleep(self.log_interval)
            for line_id, s in self.lines.items():
                if s.produced > 0 and s.produced != s.last_logged_produced:
                    self._write_log(line_id)
                    s.last_logged_produced = s.produced

    # ─── START / STOP ──────────────────────────
    def start(self):
        self._running = True
        shift = self.shift_detector.current_shift()
        logger.info(
            f"Production Logger v3 (zamanlanmış özet)\n"
            f"  Log aralığı: {self.log_interval}sn\n"
            f"  Vardiya: {shift['label']}\n"
            f"  Özet saatleri: {', '.join(s['code'] + ' → ' + str(s['latest_end']) + ':00' for s in self.shift_detector.shifts)}"
        )
        self.client.connect(self.mqtt_host, self.mqtt_port, 60)
        self.client.loop_start()

        # Log döngüsü
        threading.Thread(target=self._log_loop, daemon=True).start()

        # Vardiya özeti zamanlayıcısı
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self.client.loop_stop()
        self.client.disconnect()
        if self._db_conn: self._db_conn.close()
        logger.info("Production Logger durduruldu")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    shifts = [
        {"code": "vardiya_1", "label": "1. Vardiya",
         "start_hour": 8, "end_hour": 18, "earliest_end": 17, "latest_end": 19},
        {"code": "vardiya_2", "label": "2. Vardiya",
         "start_hour": 18, "end_hour": 24, "earliest_end": 23, "latest_end": 1},
    ]

    svc = ProductionLogger(
        db_url=os.getenv("DB_URL", "postgresql://yigitcanc:***REMOVED***@127.0.0.1:5432/cerkezkoy_db"),
        mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
        log_interval_sec=int(os.getenv("LOG_INTERVAL", "60")),
        shifts_config=shifts,
    )

    try:
        svc.start()
        logger.info("Çalışıyor — Ctrl+C ile durdur")
        while True: time.sleep(10)
    except KeyboardInterrupt:
        svc.stop()