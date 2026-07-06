from fastapi import APIRouter

from src.persistence.db import is_database_available
from src.shared.config import get_settings
from src.shared.schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        service="xoc-api",
        stage=settings.app_stage,
        database="available" if is_database_available() else "unavailable",
    )
