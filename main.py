from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from models.database import engine, Base
from models.downtime_models import Downtime, DowntimeReason
from services.mqtt_service import mqtt_service
from services.static_page_guard import line_context_redirect_url
from routers import sensors, machines, alerts, batch_transfer, websocket_router, assignments, lines, planning
from routers import downtimes, production, health, oil, scrap, reports, settings as settings_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Tablolar oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    import shift_utils
    shift_utils.configure(
        f"postgresql://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )
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
app.include_router(assignments.router)
app.include_router(lines.router)
app.include_router(planning.router)
from routers import material_reporting
app.include_router(material_reporting.router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.middleware("http")
async def static_no_cache(request, call_next):
    redirect_url = line_context_redirect_url(request.url.path, request.query_params)
    if redirect_url:
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.headers["Cache-Control"] = "no-store"
        return response

    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/", tags=["Sistem"])
async def root():
    return {"service": settings.api_title, "version": settings.api_version,
            "active_db": settings.active_db, "docs": "/docs"}

@app.get("/health", tags=["Sistem"])
async def health():
    return {"status": "healthy", "database": settings.active_db,
            "mqtt_topics": len(mqtt_service._latest_data)}
