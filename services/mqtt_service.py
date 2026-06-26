import json
import asyncio
from datetime import datetime
from paho.mqtt import client as mqtt_client
from sqlalchemy.orm import Session

from config import settings
from models.database import SessionLocal
from models.orm_models import SensorReading


class MQTTService:
    def __init__(self):
        self.client = mqtt_client.Client(
            client_id=settings.mqtt_client_id,
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._listeners: list[asyncio.Queue] = []
        self._latest_data: dict = {}

    def connect(self):
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.connect(settings.mqtt_broker, settings.mqtt_port)
        self.client.loop_start()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        print(f"✓ MQTT Broker bağlandı (rc={rc})")
        client.subscribe("fabrika/#", qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            self._latest_data[msg.topic] = {
                "payload": payload, "received_at": datetime.utcnow().isoformat()
            }
            if len(topic_parts) >= 3 and topic_parts[2] not in ("bulk", "durum"):
                self._write_to_db(topic_parts, payload)
            message = {"topic": msg.topic, "payload": payload,
                       "ts": datetime.utcnow().isoformat()}
            for queue in self._listeners:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass
        except Exception as e:
            print(f"MQTT mesaj hatası: {e}")

    def _write_to_db(self, topic_parts: list, payload: dict):
        db: Session = SessionLocal()
        try:
            reading = SensorReading(
                sensor_id=payload.get("sensor_id", 0),
                machine_id=payload.get("machine_id", 0),
                line_id=payload.get("line_id", 0),
                sensor_type=topic_parts[2],
                value=float(payload.get("value", 0)),
                quality=payload.get("quality", 100),
                device_id=payload.get("device", "unknown"),
            )
            db.add(reading)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"DB yazma hatası: {e}")
        finally:
            db.close()

    def publish(self, topic: str, payload: dict, qos: int = 1):
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def get_latest(self) -> dict:
        return self._latest_data

    def add_listener(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        self._listeners.append(queue)
        return queue

    def remove_listener(self, queue: asyncio.Queue):
        if queue in self._listeners:
            self._listeners.remove(queue)


mqtt_service = MQTTService()