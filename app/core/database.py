from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


_engine_kwargs = {
    "pool_pre_ping": True,
}
if str(settings.database_url).lower().startswith("postgresql"):
    # Configure QueuePool at engine construction time. Mutating QueuePool internals after
    # startup is not reliable and was one reason workstations could wait ~30s for a slot.
    _engine_kwargs.update({
        "pool_size": _env_int("FC_DB_POOL_SIZE", 20, 5, 40),  # FC_MEMORY_STABILITY_G5: bounded DB concurrency
        "max_overflow": _env_int("FC_DB_MAX_OVERFLOW", 10, 0, 30),
        "pool_timeout": _env_int("FC_DB_POOL_TIMEOUT", 5, 2, 15),
        "pool_recycle": _env_int("FC_DB_POOL_RECYCLE", 900, 60, 3600),
        "pool_use_lifo": True,
        "connect_args": {
            "connect_timeout": _env_int("FC_DB_CONNECT_TIMEOUT", 5, 2, 15),
            "application_name": "FulfillCore",
        },
    })

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# FC_DB_TX_CLEANUP_09415G3_20260903
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        # Session.close() also rolls back, but doing it explicitly ends any open read
        # transaction before the connection is returned to QueuePool. This is a safety
        # net for exception/serialization paths and does not commit or change data.
        try:
            if db.in_transaction():
                db.rollback()
        finally:
            db.close()
