"""
Duruş Monitor v5 — auto_cycle_on tabanlı (state-based)
services/downtime_monitor.py

DEĞİŞİKLİK (v4 → v5):
  ESKİ:  STOP_CYCLE / END_CYCLE (DB3 komut bitleri, YÜKSELEN KENAR) → duruş
  YENİ:  auto_cycle_on (DB1.DBX0.0, DURUM tabanlı) → duruş
         - Pi4 publisher 'fabrika/hat1/makine_durumu' topic'ine
             {"production_state": bool, "auto_cycle_on": bool, "ts": float}
           retain=True ile yayınlıyor.
         - auto_cycle_on = False  → grace (30sn) → otomatik duruş (BELIRLENMEDI)
         - auto_cycle_on = True   → aktif duruşu kapat

  KORUNAN:
         - EMPTY_LINE (DB3.DBX0.7, 'komut' topic) → anında BOSALTMA duruşu
         - 60dk sessizlik + sebep BELİRLENMEDİ → VARDIYA_SONU

Neden state-based?
  start_cycle / stop_cycle / end_cycle yükselen-kenar sinyalleriydi; PLC veya
  servis yeniden başlatıldığında kenar kaçtığı için güvenilmezdi.
  auto_cycle_on bir DURUM bitidir; retain + publisher heartbeat ile yeniden
  başlatma sonrası bile doğru değeri taşır. Bu yüzden kenar yerine DURUM
  karşılaştırması yapıyoruz (prev == auto ise işlem yok → heartbeat/retain
  tekrarları zararsız).

MQTT topic'leri (Pi4 → Pi3B):
  fabrika/hat1/makine_durumu  → {"production_state":bool,"auto_cycle_on":bool,"ts":float}  (retain)
  fabrika/hat1/komut          → EMPTY_LINE biti için (retain)
"""

import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
import paho.mqtt.client as mqtt
import shift_utils

logger = logging.getLogger("downtime_monitor")


class LineState:
    def __init__(self, line_id, line_name):
        self.line_id = line_id
        self.line_name = line_name

        # ── Makine durumu (state-based) ──
        self.auto_cycle_on: Optional[bool] = None   # None = henüz hiç mesaj gelmedi
        self.production_state = False

        # ── Komut bitleri (artık sadece EMPTY_LINE kullanılıyor) ──
        self.empty_line = False
        self.start_motor = False
        self.stop_motor = False

        self.last_produced = None
        self.last_produced_ts = time.time()
        self.active_is_stall = False

        # ── Gerçek duruş anı (started_at geri tarihleme için) ──
        self.stop_detected_at: Optional[float] = None   # auto_cycle_on False olduğu an
        self.produced_at_stop = None                    # durduğu andaki produced (grace teyidi)

        # ── Duruş ──
        self.active_downtime_id: Optional[int] = None
        self.active_reason_code: Optional[str] = None
        self.grace_timer: Optional[threading.Timer] = None
        self.is_in_grace = False
        self.inactivity_timer: Optional[threading.Timer] = None

    def cancel_grace(self):
        if self.grace_timer and self.grace_timer.is_alive():
            self.grace_timer.cancel()
        self.grace_timer = None
        self.is_in_grace = False

    def cancel_inactivity(self):
        if self.inactivity_timer and self.inactivity_timer.is_alive():
            self.inactivity_timer.cancel()
        self.inactivity_timer = None


class DowntimeMonitor:
    PENDING = "BELIRLENMEDI"
    SHIFT_END = "VARDIYA_SONU"
    EMPTY = "BOSALTMA"

    def __init__(self, api_base_url="http://127.0.0.1:8000", mqtt_host="127.0.0.1",
                 mqtt_port=1883, grace_sec=30, shift_end_min=60.0, stall_sec=180, lines_config=None):
        self.api_url = api_base_url.rstrip("/")
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.grace_sec = grace_sec
        self.shift_end_sec = shift_end_min * 60
        self.stall_sec = stall_sec     # auto_cycle_on açıkken üretim olmadan geçen sınır

        lines_config = lines_config or [{"line_id": 1, "line_name": "Qs Hattı", "mqtt_prefix": "fabrika/hat1"}]

        self.lines = {}
        self.prefix_map = {}
        for c in lines_config:
            s = LineState(c["line_id"], c["line_name"])
            self.lines[str(c["line_id"])] = s
            self.prefix_map[c["mqtt_prefix"]] = s

        self.client = mqtt.Client(client_id="downtime_monitor_v5", protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self._reason_ids = {}  # code → id
        self._running = False

    # ── MQTT ──
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("MQTT bağlantısı kuruldu")
            for pfx in self.prefix_map:
                client.subscribe(f"{pfx}/makine_durumu")   # ← YENİ ana sinyal (auto_cycle_on)
                client.subscribe(f"{pfx}/komut")            # ← sadece EMPTY_LINE için
                client.subscribe(f"{pfx}/uretim")
                client.subscribe(f"{pfx}/config")          # ← vardiya/ayar reload
                logger.info(f"  Abone: {pfx}/makine_durumu , {pfx}/komut , {pfx}/config")
        else:
            logger.error(f"MQTT hata: rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        logger.warning(f"MQTT kesildi: rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            parts = msg.topic.rsplit("/", 1)
            if len(parts) != 2:
                return
            pfx, sig = parts
            state = self.prefix_map.get(pfx)
            if not state:
                return

            if sig == "makine_durumu":
                self._handle_machine_state(state, payload)
            elif sig == "komut":
                self._handle_command(state, payload)
            elif sig == "uretim":
                self._handle_uretim(state, payload)
            elif sig == "config":
                shift_utils.invalidate()
                logger.info(f"[{state.line_name}] config reload — vardiya cache tazelendi")
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Mesaj hatası: {e}", exc_info=True)

    # ── ANA MANTIK: auto_cycle_on (DURUM tabanlı) ──
    def _handle_machine_state(self, s: LineState, p: dict):
        auto = bool(p.get("auto_cycle_on", False))
        prev = s.auto_cycle_on
        s.auto_cycle_on = auto
        s.production_state = bool(p.get("production_state", False))

        # Durum değişmediyse (heartbeat / retain tekrarı) → hiçbir şey yapma
        if prev == auto:
            return

        if auto:
            # ── auto_cycle_on=True TEK BAŞINA güvenilmez ──
            # El kumandasından manuel jog da bu biti açar. Bu yüzden burada duruşu
            # KAPATMIYORUZ ve grace'i İPTAL ETMİYORUZ. Gerçek üretim teyidi yalnızca
            # 'uretim' sinyalinden (produced artışı) gelir — kapatma orada yapılır.
            logger.info(f"[{s.line_name}] auto_cycle_on: {prev} → True (sinyal var, üretim teyidi bekleniyor)")
        else:
            # ── Makine durdu → gerçek duruş anını kaydet → grace → duruş ──
            logger.info(f"[{s.line_name}] auto_cycle_on: {prev} → False (durdu) → grace ({self.grace_sec}sn)")
            s.stop_detected_at = time.time()
            s.produced_at_stop = s.last_produced
            if not s.active_downtime_id and not s.is_in_grace:
                self._start_grace(s)

    # ── EMPTY_LINE: 'komut' topic'inden (v4'ten korundu) ──
    def _handle_command(self, s: LineState, p: dict):
        old_empty = s.empty_line
        s.empty_line = bool(p.get("empty_line", False))
        s.start_motor = bool(p.get("start_motor", False))
        s.stop_motor = bool(p.get("stop_motor", False))

        # EMPTY_LINE yükselen kenar → anında BOSALTMA
        if s.empty_line and not old_empty:
            logger.info(f"[{s.line_name}] EMPTY_LINE ↑ → anında duruş (BOSALTMA)")
            s.cancel_grace()
            if not s.active_downtime_id:
                self._auto_start(s, reason_code=self.EMPTY, trigger="empty_line")
    def _handle_uretim(self, s: LineState, p: dict):
        produced = p.get("produced")
        if produced is None:
            return
        now = time.time()

        # Üretim sayacı değiştiyse zamanı sıfırla (reset dahil)
        if s.last_produced is None or produced != s.last_produced:
            advanced = (s.last_produced is not None and produced > s.last_produced)
            s.last_produced = produced
            s.last_produced_ts = now
            # GERÇEK üretim ilerledi mi? (reset/azalma değil) → makine gerçekten çalışıyor
            if advanced:
                # Grace sırasındaysa: gerçek parça çıktı → duruş hiç açılmasın
                if s.is_in_grace:
                    logger.info(f"[{s.line_name}] Grace sırasında üretim ilerledi → grace iptal, duruş açılmıyor")
                    s.cancel_grace()
                # Aktif duruş varsa (türü ne olursa olsun) → kapat
                if s.active_downtime_id:
                    logger.info(f"[{s.line_name}] Üretim ilerledi → duruş kapatılıyor")
                    self._auto_stop(s, "Üretim devam etti — otomatik")
            return

        # produced DEĞİŞMEDİ — sadece makine 'çalışıyor' görünürken stall kontrolü
        if self.stall_sec > 0 and s.auto_cycle_on is True and not s.active_downtime_id and not s.is_in_grace:
            if now - s.last_produced_ts >= self.stall_sec:
                logger.warning(
                    f"[{s.line_name}] auto_cycle_on=True ama {self.stall_sec}sn üretim yok → STALL DURUŞU")
                self._auto_start(
                    s, is_stall=True,
                    note=f"Otomatik — üretim durdu ({self.stall_sec}sn parça çıkmadı, auto_cycle_on açık)",
                    trigger="stall", started_at_ts=s.last_produced_ts)
                self._start_inactivity(s)
    def _start_grace(self, s: LineState):
        if s.active_downtime_id:
            logger.info("  Zaten aktif duruş var — grace atlanıyor")
            return
        s.cancel_grace()
        s.is_in_grace = True
        s.grace_timer = threading.Timer(self.grace_sec, self._grace_done, args=[s])
        s.grace_timer.daemon = True
        s.grace_timer.start()

    def _grace_done(self, s: LineState):
        s.is_in_grace = False
        # Grace süresince GERÇEK üretim oldu mu? (auto_cycle_on değil — produced artışı)
        # Manuel jog auto_cycle_on'u açabilir ama parça çıkarmaz; ölçüt produced.
        if (s.produced_at_stop is not None and s.last_produced is not None
                and s.last_produced > s.produced_at_stop):
            logger.info(f"[{s.line_name}] Grace bitti ama üretim ilerlemiş — duruş açılmadı")
            return
        logger.warning(f"[{s.line_name}] Grace doldu (üretim yok) → OTOMATİK DURUŞ")
        # started_at = gerçek duruş anı (stop_detected_at) → grace süresi (180sn) duruşa dahil
        self._auto_start(s, trigger="grace", started_at_ts=s.stop_detected_at)
        self._start_inactivity(s)

    def _start_inactivity(self, s: LineState):
        s.cancel_inactivity()
        s.inactivity_timer = threading.Timer(self.shift_end_sec, self._inactivity_done, args=[s])
        s.inactivity_timer.daemon = True
        s.inactivity_timer.start()

    def _inactivity_done(self, s: LineState):
        if not s.active_downtime_id or s.auto_cycle_on:
            return
        if s.active_reason_code and s.active_reason_code != self.PENDING:
            logger.info(f"[{s.line_name}] İnaktif ama sebep var ({s.active_reason_code}) — kapatılmıyor")
            return
        # ── Sadece GERÇEK vardiya bitişine yakınsa VARDIYA_SONU yap ──
        # Vardiya bitiş saati tek kaynaktan (shift_config.end_hour).
        h = datetime.now().hour
        sh = shift_utils.shift_by_code(shift_utils.detect_shift_code())
        end_h = sh["end_hour"] % 24
        if h < end_h:
            logger.info(f"[{s.line_name}] 60dk sessizlik ama vardiya ortası (saat {h}/{end_h}) — duruş açık tutuluyor")
        self._update_reason_api(s, self.SHIFT_END)
        self._auto_stop(s, "Vardiya sonu — otomatik")

    # ── API (v4 ile aynı) ──
    def _auto_start(self, s: LineState, reason_code: str = None, is_stall: bool = False,
                    note: str = None, trigger: str = None, started_at_ts: float = None):
        import requests
        code = reason_code or self.PENDING
        rid = self._reason_ids.get(code)
        if not rid:
            logger.error(f"'{code}' reason_id bulunamadı"); return
        body = {"line_id": s.line_id, "reason_id": rid, "shift": shift_utils.detect_shift_code(),
                "notes": note or f"Otomatik — {code}", "trigger": trigger}
        # Gerçek duruş anı verildiyse started_at olarak gönder (grace/stall geri tarihleme)
        if started_at_ts:
            body["started_at"] = datetime.fromtimestamp(started_at_ts, tz=timezone.utc).isoformat()
        try:
            r = requests.post(f"{self.api_url}/api/v1/downtimes/start",
                json=body, timeout=5)
            if r.status_code == 201:
                d = r.json()
                s.active_downtime_id = d["downtime_id"]
                s.active_reason_code = code
                s.active_is_stall = is_stall
                logger.info(f"[{s.line_name}] Duruş: ID={s.active_downtime_id} ({code})"
                            + (" [stall]" if is_stall else ""))
            elif r.status_code == 409:
                self._sync(s)
        except Exception as e:
            logger.error(f"API start: {e}")

    def _auto_stop(self, s: LineState, note="Otomatik"):
        import requests
        if not s.active_downtime_id:
            return
        try:
            r = requests.post(f"{self.api_url}/api/v1/downtimes/{s.active_downtime_id}/stop",
                json={"notes": note}, timeout=5)
            if r.status_code == 200:
                logger.info(f"[{s.line_name}] Kapatıldı: ID={s.active_downtime_id}, {r.json().get('duration_text','?')}")
        except Exception as e:
            logger.error(f"API stop: {e}")
        finally:
            s.active_downtime_id = None; s.active_reason_code = None
            s.active_is_stall = False; s.cancel_inactivity()

    def _update_reason_api(self, s: LineState, code: str):
        import requests
        rid = self._reason_ids.get(code)
        if not rid or not s.active_downtime_id:
            return
        try:
            requests.patch(f"{self.api_url}/api/v1/downtimes/{s.active_downtime_id}/reason",
                params={"reason_id": rid}, timeout=5)
            s.active_reason_code = code
        except Exception:
            pass

    def _sync(self, s: LineState):
        import requests
        try:
            r = requests.get(f"{self.api_url}/api/v1/downtimes/active/{s.line_id}", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d and d.get("downtime_id"):
                    s.active_downtime_id = d["downtime_id"]
                    s.active_reason_code = d.get("reason_code", "")
        except Exception:
            pass

    def _fetch_reasons(self):
        import requests
        try:
            r = requests.get(f"{self.api_url}/api/v1/downtimes/reasons", timeout=5)
            if r.status_code == 200:
                for x in r.json():
                    self._reason_ids[x["reason_code"]] = x["reason_id"]
        except Exception as e:
            logger.error(f"Sebepler: {e}")

    # Periyodik sebep sync (operatör terminalden sebep girince yakala)
    def _reason_check_loop(self):
        import requests
        while self._running:
            time.sleep(30)
            # Açılışta API hazır değilse sebepler boş kalmış olabilir → tekrar dene
            if not self._reason_ids:
                logger.warning("reason_ids boş — sebepler yeniden çekiliyor")
                self._fetch_reasons()
            for s in self.lines.values():
                if not s.active_downtime_id:
                    continue
                try:
                    r = requests.get(f"{self.api_url}/api/v1/downtimes/active/{s.line_id}", timeout=5)
                    if r.status_code == 200:
                        d = r.json()
                        if d and d.get("reason_code") != s.active_reason_code:
                            old = s.active_reason_code
                            s.active_reason_code = d["reason_code"]
                            logger.info(f"[{s.line_name}] Sebep: {old} → {s.active_reason_code}")
                            if s.active_reason_code != self.PENDING:
                                s.cancel_inactivity()
                except Exception:
                    pass

    # ── START / STOP ──
    def start(self):
        self._running = True
        logger.info(f"Monitor v5 (auto_cycle_on) — Grace:{self.grace_sec}sn, Shift-end:{self.shift_end_sec/60:.0f}dk")

        self.client.connect(self.mqtt_host, self.mqtt_port, 60)
        self.client.loop_start()

        for i in range(5):
            self._fetch_reasons()
            if self._reason_ids:
                break
            time.sleep(3)

        for code in [self.PENDING, self.SHIFT_END]:
            if code not in self._reason_ids:
                logger.error(f"'{code}' sebebi bulunamadı!")

        for s in self.lines.values():
            self._sync(s)
            if s.active_downtime_id:
                logger.info(f"[{s.line_name}] Mevcut: ID={s.active_downtime_id} ({s.active_reason_code})")
                if s.active_reason_code == self.PENDING:
                    self._start_inactivity(s)

        threading.Thread(target=self._reason_check_loop, daemon=True).start()

    def stop(self):
        self._running = False
        for s in self.lines.values():
            s.cancel_grace()
            s.cancel_inactivity()
        self.client.loop_stop()
        self.client.disconnect()


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    m = DowntimeMonitor(
        api_base_url=os.getenv("API_URL", "http://127.0.0.1:8000"),
        mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
        grace_sec=int(os.getenv("GRACE_PERIOD", "30")),
        shift_end_min=float(os.getenv("SHIFT_END_TIMEOUT", "60")),
        stall_sec=int(os.getenv("STALL_TIMEOUT", "180")),
    )
    try:
        m.start()
        logger.info("Monitor v5 çalışıyor")
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        m.stop()