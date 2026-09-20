from collections.abc import Generator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine, get_db


def test_engine_is_configured_for_postgresql() -> None:
    assert isinstance(engine, Engine)
    assert engine.url.get_backend_name() == "postgresql"
    assert engine.url.get_driver_name() == "psycopg"
    assert engine.pool._pre_ping is True


def test_session_factory_uses_application_engine() -> None:
    assert SessionLocal.kw["bind"] is engine
    assert SessionLocal.kw["autoflush"] is False
    assert SessionLocal.kw["expire_on_commit"] is False


def test_get_db_closes_session() -> None:
    dependency: Generator[Session] = get_db()
    session = next(dependency)

    dependency.close()

    assert session.is_active is True
