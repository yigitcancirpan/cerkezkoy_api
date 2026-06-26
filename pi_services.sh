#!/bin/bash
# ============================================================
# TÜM SERVİSLERİ KUR — Pi 3B (API Sunucu)
# Bu scripti Pi 3B'de çalıştır:
#   chmod +x kurulum_servisler.sh
#   sudo bash kurulum_servisler.sh
# ============================================================

echo "═══════════════════════════════════════"
echo "  Pi 3B — Servis Kurulumu"
echo "═══════════════════════════════════════"

# ─── 1) FastAPI ────────────────────────────
echo ""
echo "1) cerkezkoy_api.service oluşturuluyor..."

cat > /etc/systemd/system/cerkezkoy_api.service << 'EOF'
[Unit]
Description=Fabrika IoT — FastAPI
After=network.target postgresql.service mosquitto.service
Wants=mosquitto.service postgresql.service

[Service]
Type=simple
User=server
WorkingDirectory=/home/server/cerkezkoy_api
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "   ✓ cerkezkoy_api.service"

# ─── 2) Downtime Monitor ──────────────────
echo "2) downtime_monitor.service oluşturuluyor..."

cat > /etc/systemd/system/downtime_monitor.service << 'EOF'
[Unit]
Description=Fabrika IoT — Duruş Monitor
After=network.target mosquitto.service cerkezkoy_api.service
Wants=mosquitto.service cerkezkoy_api.service

[Service]
Type=simple
User=server
WorkingDirectory=/home/server/cerkezkoy_api
Environment=API_URL=http://127.0.0.1:8000
Environment=MQTT_HOST=127.0.0.1
Environment=MQTT_PORT=1883
Environment=GRACE_PERIOD=180
Environment=SHIFT_END_TIMEOUT=60
Environment=STALL_TIMEOUT=0
ExecStart=/usr/bin/python3 services/downtime_monitor.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "   ✓ downtime_monitor.service"

# ─── 3) Production Logger ─────────────────
echo "3) production_logger.service oluşturuluyor..."

cat > /etc/systemd/system/production_logger.service << 'EOF'
[Unit]
Description=Fabrika IoT — Production Logger
After=network.target mosquitto.service postgresql.service cerkezkoy_api.service
Wants=mosquitto.service postgresql.service

[Service]
Type=simple
User=server
WorkingDirectory=/home/server/cerkezkoy_api
Environment=DB_URL=postgresql://yigitcanc:***REMOVED***@127.0.0.1:5432/cerkezkoy_db
Environment=MQTT_HOST=127.0.0.1
Environment=LOG_INTERVAL=60
Environment=SHIFT_END_SILENCE=30
ExecStart=/usr/bin/python3 services/production_logger.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "   ✓ production_logger.service"

# ─── Aktif et ──────────────────────────────
echo ""
echo "Servisler etkinleştiriliyor..."
systemctl daemon-reload
systemctl enable cerkezkoy_api
systemctl enable downtime_monitor
systemctl enable production_logger

echo ""
echo "Servisler başlatılıyor..."
systemctl start cerkezkoy_api
sleep 3
systemctl start downtime_monitor
systemctl start production_logger

echo ""
echo "═══════════════════════════════════════"
echo "  Durum Kontrolü"
echo "═══════════════════════════════════════"
echo "PostgreSQL:       $(systemctl is-active postgresql)"
echo "Mosquitto:        $(systemctl is-active mosquitto)"
echo "FastAPI:          $(systemctl is-active cerkezkoy_api)"
echo "Downtime Monitor: $(systemctl is-active downtime_monitor)"
echo "Production Logger:$(systemctl is-active production_logger)"
echo ""
echo "Log takibi:"
echo "  journalctl -u cerkezkoy_api -f"
echo "  journalctl -u downtime_monitor -f"
echo "  journalctl -u production_logger -f"
echo ""

# ⚠ production_logger.service içindeki DB_URL şifresini değiştirmeyi unutma!
echo "⚠ /etc/systemd/system/production_logger.service"
echo "  içindeki SIFREN'i kendi şifrenle değiştir:"
echo "  sudo nano /etc/systemd/system/production_logger.service"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart production_logger"