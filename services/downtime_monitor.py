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
import os
import threading
from datetime import datetime, timezone
from typing import Optional
import paho.mqtt.client as mqtt
import requests
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
        self._api_lock = threading.RLock()
        self._http = requests.Session()

    def _request(self, method: str, path: str, **kwargs):
        """Yerel API isteklerini tek oturumda ve thread-safe olarak yap."""
        kwargs.setdefault("timeout", 5)
        with self._api_lock:
            return self._http.request(method, f"{self.api_url}{path}", **kwargs)

    @staticmethod
    def _clear_active_state(s: LineState):
        """API'de aktif kayıt kalmadığında yerel duruş state'ini temizle."""
        s.active_downtime_id = None
        s.active_reason_code = None
        s.active_is_stall = False
        s.cancel_inactivity()

    # ── MQTT ──
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("MQTT bağlantısı kuruldu")
            client.subscribe("fabrika/config")
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
            # Terminal/API tarafında kapanmış bir kaydın eski ID'si bellekte
            # kalmış olabilir. Grace kararından önce DB ile uzlaş.
            self._sync(s)
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
                    # Kayıt terminalden kapatılmış/değiştirilmiş olabilir.
                    # Kapatmadan önce gerçek aktif ID'yi API'den tazele.
                    self._sync(s)
                if s.active_downtime_id:
                    logger.info(f"[{s.line_name}] Üretim ilerledi → duruş kapatılıyor")
                    self._auto_stop(s, "Üretim devam etti — otomatik")
            return

        # produced DEĞİŞMEDİ — sadece makine 'çalışıyor' görünürken stall kontrolü
        if self.stall_sec > 0 and s.auto_cycle_on is True and not s.active_downtime_id and not s.is_in_grace:
            if now - s.last_produced_ts >= self.stall_sec:
                logger.warning(
                    f"[{s.line_name}] auto_cycle_on=True ama {self.stall_sec}sn üretim yok → STALL DURUŞU")
                started = self._auto_start(
                    s, is_stall=True,
                    note=f"Otomatik — üretim durdu ({self.stall_sec}sn parça çıkmadı, auto_cycle_on açık)",
                    trigger="stall", started_at_ts=s.last_produced_ts)
                if started:
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
        # Grace sırasında terminal başka bir aktif duruş açmış olabilir.
        self._sync(s)
        if s.active_downtime_id:
            logger.info(
                f"[{s.line_name}] Grace doldu ancak DB'de aktif duruş var "
                f"(ID={s.active_downtime_id}) — yeni kayıt açılmadı"
            )
            return
        logger.warning(f"[{s.line_name}] Grace doldu (üretim yok) → OTOMATİK DURUŞ")
        # started_at = gerçek duruş anı (stop_detected_at) → grace süresi (180sn) duruşa dahil
        if self._auto_start(s, trigger="grace", started_at_ts=s.stop_detected_at):
            self._start_inactivity(s)
        elif s.auto_cycle_on is False and not s.active_downtime_id:
            # Geçici API/DB hatasında aynı fiziksel duruşu kaybetme. Üretim
            # başlarsa _handle_uretim bu timer'ı iptal eder.
            retry_sec = min(30, max(5, self.grace_sec))
            logger.warning(
                f"[{s.line_name}] Otomatik duruş kaydedilemedi — "
                f"{retry_sec}sn sonra yeniden denenecek"
            )
            s.is_in_grace = True
            s.grace_timer = threading.Timer(retry_sec, self._grace_done, args=[s])
            s.grace_timer.daemon = True
            s.grace_timer.start()

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
            self._start_inactivity(s)   # bir sonraki 60dk penceresinde tekrar bak
            return
        self._update_reason_api(s, self.SHIFT_END)
        self._auto_stop(s, "Vardiya sonu — otomatik")

    # ── API (v4 ile aynı) ──
    def _auto_start(self, s: LineState, reason_code: str = None, is_stall: bool = False,
                    note: str = None, trigger: str = None, started_at_ts: float = None):
        code = reason_code or self.PENDING
        rid = self._reason_ids.get(code)
        if not rid:
            logger.error(f"'{code}' reason_id bulunamadı")
            return False
        body = {"line_id": s.line_id, "reason_id": rid, "shift": shift_utils.detect_shift_code(),
                "notes": note or f"Otomatik — {code}", "trigger": trigger}
        # Gerçek duruş anı verildiyse started_at olarak gönder (grace/stall geri tarihleme)
        if started_at_ts:
            body["started_at"] = datetime.fromtimestamp(started_at_ts, tz=timezone.utc).isoformat()
        try:
            r = self._request("POST", "/api/v1/downtimes/start", json=body)
            if r.status_code == 201:
                d = r.json()
                s.active_downtime_id = d["downtime_id"]
                s.active_reason_code = code
                s.active_is_stall = is_stall
                logger.info(f"[{s.line_name}] Duruş: ID={s.active_downtime_id} ({code})"
                            + (" [stall]" if is_stall else ""))
                return True
            elif r.status_code == 409:
                logger.warning(f"[{s.line_name}] Duruş açılamadı (409) — DB'de zaten açık duruş var, senkronize ediliyor")
                self._sync(s)
                # Bayat vardiya-sonu duruşu önü tıkıyorsa: makine YENİ vardiyada tekrar durdu
                # demektir → eskisini kapat, asıl duruşu aç
                if s.active_reason_code == self.SHIFT_END:
                    logger.warning(f"[{s.line_name}] Önde bayat VARDIYA_SONU (ID={s.active_downtime_id}) — kapatılıp gerçek duruş açılıyor")
                    self._auto_stop(s, "Bayat vardiya sonu — yeni duruş öncesi otomatik kapatıldı")
                    if not s.active_downtime_id:   # kapatma başarılıysa tekrar dene
                        return self._auto_start(
                            s,
                            reason_code=code,
                            is_stall=is_stall,
                            note=note,
                            trigger=trigger,
                            started_at_ts=started_at_ts,
                        )
                return bool(s.active_downtime_id)
            else:
                logger.error(
                    f"[{s.line_name}] Duruş açılamadı: HTTP {r.status_code} "
                    f"{r.text[:200]}"
                )
        except Exception as e:
            logger.error(f"[{s.line_name}] API start hatası: {e}")
        return False

    def _auto_stop(self, s: LineState, note="Otomatik"):
        if not s.active_downtime_id:
            return
        attempted_id = s.active_downtime_id
        try:
            r = self._request(
                "POST",
                f"/api/v1/downtimes/{attempted_id}/stop",
                json={"notes": note},
            )
            if r.status_code == 200:
                logger.info(f"[{s.line_name}] Kapatıldı: ID={attempted_id}, {r.json().get('duration_text','?')}")
                self._clear_active_state(s)
            elif r.status_code == 404:
                logger.warning(
                    f"[{s.line_name}] ID={attempted_id} API'de artık aktif değil "
                    "(404) — state yeniden senkronize ediliyor"
                )
                self._sync(s)
            else:
                logger.warning(f"[{s.line_name}] Duruş KAPATILAMADI: ID={attempted_id} "
                               f"HTTP {r.status_code} {r.text[:200]} — state korunuyor, tekrar denenecek")
        except Exception as e:
            logger.error(f"[{s.line_name}] API stop hatası: {e} — state korunuyor")

    def _update_reason_api(self, s: LineState, code: str):
        rid = self._reason_ids.get(code)
        if not rid or not s.active_downtime_id:
            return
        try:
            r = self._request(
                "PATCH",
                f"/api/v1/downtimes/{s.active_downtime_id}/reason",
                params={"reason_id": rid},
            )
            if r.status_code == 200:
                s.active_reason_code = code
            elif r.status_code == 404:
                logger.warning(f"[{s.line_name}] Sebep güncellenecek duruş bulunamadı — senkronize ediliyor")
                self._sync(s)
            else:
                logger.warning(
                    f"[{s.line_name}] Sebep güncellenemedi: HTTP {r.status_code} "
                    f"{r.text[:200]}"
                )
        except Exception as e:
            logger.error(f"[{s.line_name}] API reason hatası: {e}")

    def _sync(self, s: LineState):
        try:
            r = self._request("GET", f"/api/v1/downtimes/active/{s.line_id}")
            if r.status_code == 200:
                d = r.json()
                if d and d.get("downtime_id"):
                    old_id = s.active_downtime_id
                    old_reason = s.active_reason_code
                    s.active_downtime_id = d["downtime_id"]
                    s.active_reason_code = d.get("reason_code", "")
                    s.cancel_grace()
                    if old_id != s.active_downtime_id:
                        logger.info(
                            f"[{s.line_name}] Aktif duruş senkronize edildi: "
                            f"{old_id} → {s.active_downtime_id}"
                        )
                    if old_reason != s.active_reason_code:
                        logger.info(
                            f"[{s.line_name}] Sebep: {old_reason} → "
                            f"{s.active_reason_code}"
                        )
                    if s.active_reason_code != self.PENDING:
                        s.cancel_inactivity()
                    elif old_id != s.active_downtime_id:
                        self._start_inactivity(s)
                else:
                    if s.active_downtime_id:
                        logger.warning(
                            f"[{s.line_name}] Bayat yerel duruş state'i temizlendi: "
                            f"ID={s.active_downtime_id}"
                        )
                    self._clear_active_state(s)
                return True
            logger.warning(
                f"[{s.line_name}] Aktif duruş senkronizasyonu başarısız: "
                f"HTTP {r.status_code} {r.text[:200]}"
            )
        except Exception as e:
            logger.error(f"[{s.line_name}] API sync hatası: {e}")
        return False

    def _fetch_reasons(self):
        try:
            r = self._request("GET", "/api/v1/downtimes/reasons")
            if r.status_code == 200:
                for x in r.json():
                    self._reason_ids[x["reason_code"]] = x["reason_id"]
            else:
                logger.warning(f"Sebepler alınamadı: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            logger.error(f"Sebepler: {e}")

    # Periyodik sebep sync (operatör terminalden sebep girince yakala)
    def _reason_check_loop(self):
        while self._running:
            time.sleep(30)
            # Açılışta API hazır değilse sebepler boş kalmış olabilir → tekrar dene
            if not self._reason_ids:
                logger.warning("reason_ids boş — sebepler yeniden çekiliyor")
                self._fetch_reasons()
            for s in self.lines.values():
                # Sadece bellekte aktif görünenleri değil, tüm izlenen hatları
                # uzlaştır: terminalden açma/kapama ve bayat ID'ler yakalanır.
                self._sync(s)

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
        self._http.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    from line_utils import load_lines_config
    lines = load_lines_config(os.environ["DB_URL"])
    if not lines:
        raise SystemExit("production_lines'ta aktif hat yok")

    monitored_raw = os.getenv("MONITORED_LINE_IDS", "").strip()
    if monitored_raw:
        try:
            monitored_ids = {
                int(value.strip())
                for value in monitored_raw.split(",")
                if value.strip()
            }
        except ValueError as exc:
            raise SystemExit(
                "MONITORED_LINE_IDS virgülle ayrılmış sayılardan oluşmalı"
            ) from exc

        known_ids = {int(line["line_id"]) for line in lines}
        missing_ids = monitored_ids - known_ids
        if missing_ids:
            raise SystemExit(
                f"MONITORED_LINE_IDS içinde tanımsız/pasif hat var: "
                f"{sorted(missing_ids)}"
            )
        lines = [line for line in lines if int(line["line_id"]) in monitored_ids]
        if not lines:
            raise SystemExit("MONITORED_LINE_IDS sonrasında izlenecek hat kalmadı")

    logger.info(
        "Otomatik duruş izlenen hatlar: %s",
        ", ".join(f"{line['line_id']}:{line['line_name']}" for line in lines),
    )

    m = DowntimeMonitor(
        api_base_url=os.getenv("API_URL", "http://127.0.0.1:8000"),
        mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        grace_sec=int(os.getenv("GRACE_PERIOD", "30")),
        shift_end_min=float(os.getenv("SHIFT_END_TIMEOUT", "60")),
        stall_sec=int(os.getenv("STALL_TIMEOUT", "180")),
        lines_config=lines,
    )
    try:
        m.start()
        logger.info("Monitor v5 çalışıyor")
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        m.stop()
