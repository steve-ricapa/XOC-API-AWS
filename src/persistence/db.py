from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.shared.config import get_database_url
from src.shared.errors import ConfigurationError


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    database_url = get_database_url()
    if not database_url:
        raise ConfigurationError("Database URL is not configured")

    _engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=1,
        max_overflow=0,
        future=True,
    )
    _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def is_database_available() -> bool:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@contextmanager
def session_scope() -> Iterator[Session]:
    global _session_factory

    if _session_factory is None:
        get_engine()
    if _session_factory is None:
        raise ConfigurationError("Database session factory is not configured")

    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Iterator[Session]:
    global _session_factory

    if _session_factory is None:
        get_engine()
    if _session_factory is None:
        raise ConfigurationError("Database session factory is not configured")

    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
