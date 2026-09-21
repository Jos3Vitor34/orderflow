from fastapi import FastAPI

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import CorrelationIdMiddleware

settings = get_settings()
configure_logging(settings)
app = FastAPI(title="OrderFlow API", version="0.1.0")
app.add_middleware(
    CorrelationIdMiddleware,
    max_length=settings.correlation_id_max_length,
)
app.include_router(api_router)
app.include_router(health_router)
