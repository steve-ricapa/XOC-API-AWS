from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.handlers.routes.admin import router as admin_router
from src.handlers.routes.agents import router as agents_router
from src.handlers.routes.audit import router as audit_router
from src.handlers.routes.auth import router as auth_router
from src.handlers.routes.chat import router as chat_router
from src.handlers.routes.alerts import router as alerts_router
from src.handlers.routes.analytics import router as analytics_router
from src.handlers.routes.companies import router as companies_router
from src.handlers.routes.health import router as health_router
from src.handlers.routes.integrations import router as integrations_router
from src.handlers.routes.scans import router as scans_router
from src.handlers.routes.superadmin import router as superadmin_router
from src.handlers.routes.systems import router as systems_router
from src.handlers.routes.tickets import router as tickets_router
from src.handlers.routes.users import router as users_router
from src.handlers.routes.vulnerabilities import router as vulnerabilities_router
from src.shared.config import get_settings
from src.shared.errors import AppError
from src.shared.logging import logger


settings = get_settings()

app = FastAPI(
    title="XOC API",
    version="1.0.0",
    docs_url="/docs" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id", "X-Superadmin-Confirm"],
    expose_headers=["X-Request-Id"],
)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "code": exc.code})


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled FastAPI error")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "code": "internal_error"})


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(agents_router)
app.include_router(audit_router)
app.include_router(chat_router)
app.include_router(companies_router)
app.include_router(users_router)
app.include_router(tickets_router)
app.include_router(integrations_router)
app.include_router(scans_router)
app.include_router(superadmin_router)
app.include_router(systems_router)
app.include_router(alerts_router)
app.include_router(vulnerabilities_router)
app.include_router(analytics_router)

handler = Mangum(app)
