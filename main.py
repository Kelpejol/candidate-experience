"""Application entry point.

Builds the FastAPI app, wires up all API routers (health, call records,
voice, voice agent webhooks, campaigns, jobs), and registers a startup
hook that creates the database tables.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.api.routes.call_records import router as call_records_router
from app.core.database import create_db_and_tables
from app.api.routes.voice import router as voice_router
from app.api.routes.voice_agent_webhooks import router as voice_agent_webhooks_router
from app.api.routes.campaigns import router as campaign_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.helpdesk_webhooks import router as helpdesk_webhooks_router
from app.api.routes.helpdesk_reports import router as helpdesk_reports_router
from app.api.routes.csat import router as csat_router
from app.api.routes.voice_kb import router as voice_kb_router
from app.api.routes.voice_campaign import router as voice_campaign_router
from app.api.routes.campaign_admin import router as campaign_admin_router
from app.api.routes.voice_collect import router as voice_collect_router
from app.api.routes.voice_outbound import router as voice_outbound_router
from app.api.routes.llm_proxy import router as llm_proxy_router

settings = get_settings()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

# Allow the browser-based frontend (different origin) to call this API.
# Origins are configured via CORS_ALLOW_ORIGINS in settings.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    """FastAPI startup hook that ensures all database tables exist.

    Side effect: creates the SQLite database file/tables on first run via
    `create_db_and_tables`.
    """
    create_db_and_tables()

app.include_router(health_router)
app.include_router(call_records_router)
app.include_router(voice_router)
app.include_router(voice_agent_webhooks_router)
app.include_router(campaign_router)
app.include_router(jobs_router)
app.include_router(helpdesk_webhooks_router)
app.include_router(helpdesk_reports_router)
app.include_router(csat_router)
app.include_router(voice_kb_router)
app.include_router(voice_campaign_router)
app.include_router(campaign_admin_router)
app.include_router(voice_collect_router)
app.include_router(voice_outbound_router)
app.include_router(llm_proxy_router)
