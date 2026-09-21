from collections.abc import Generator
from math import ceil

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    str(settings.database_url),
    pool_pre_ping=True,
    connect_args={"connect_timeout": ceil(settings.readiness_timeout_seconds)},
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session]:
    """Provide a transaction-scoped database session to FastAPI dependencies."""
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
