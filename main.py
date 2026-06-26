from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from models.database import engine, Base
from models.downtime_models import Downtime, DowntimeReason
from services.mqtt_service import mqtt_service
from routers import sensors, machines, alerts, batch_transfer, websocket_router
from routers import downtimes, production, health, oil, scrap, reports, settings as settings_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Tablolar oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    print("MQTT bağlanıyor...")
    mqtt_service.connect()
    yield
    mqtt_service.disconnect()

app = FastAPI(title=settings.api_title, version=settings.api_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(sensors.router)
app.include_router(machines.router)
app.include_router(alerts.router)
app.include_router(batch_transfer.router)
app.include_router(websocket_router.router)
app.include_router(downtimes.router)
app.include_router(production.router)
app.include_router(health.router)
app.include_router(oil.router)
app.include_router(scrap.router)
app.include_router(reports.router)
app.include_router(settings_router.router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", tags=["Sistem"])
async def root():
    return {"service": settings.api_title, "version": settings.api_version,
            "active_db": settings.active_db, "docs": "/docs"}

@app.get("/health", tags=["Sistem"])
async def health():
    return {"status": "healthy", "database": settings.active_db,
            "mqtt_topics": len(mqtt_service._latest_data)}