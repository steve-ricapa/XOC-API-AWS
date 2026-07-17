from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.handlers.routes.admin import router as admin_router
from src.handlers.routes.superadmin import router as superadmin_router
from src.handlers.routes.xoc_ops import router as xoc_ops_router
from src.shared.config import get_settings
from src.shared.errors import AppError
from src.shared.logging import logger

settings = get_settings()

app = FastAPI(
    title="XOC Admin API",
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
    logger.exception("Unhandled error in Admin API")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "code": "internal_error"})


app.include_router(admin_router)
app.include_router(superadmin_router)
app.include_router(xoc_ops_router)

handler = Mangum(app)
