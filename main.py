from fastapi import FastAPI
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.api.routes.call_records import router as call_records_router
from app.core.database import create_db_and_tables
from app.api.routes.voice import router as voice_router
from app.api.routes.voice_agent_webhooks import router as voice_agent_webhooks_router
from app.api.routes.campaigns import router as campaign_router

settings = get_settings()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

app.include_router(health_router)
app.include_router(call_records_router)
app.include_router(voice_router)
app.include_router(voice_agent_webhooks_router)
app.include_router(campaign_router)