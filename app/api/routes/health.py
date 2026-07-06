from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()


@router.get("/health", tags=["Health"], description="Health check endpoint")
def health_check():
    settings = get_settings()
    return {"status": "ok", "environment": settings.env}
