from __future__ import annotations

import os
from typing import Any

from sqlalchemy.orm import Session


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def tune_db_pool(db: Session) -> dict[str, Any]:
    """Report the pool configured by ``core.database``.

    Older builds changed private QueuePool fields at runtime. Queue internals are not a
    supported configuration API and resizing them after connections exist can behave
    inconsistently. Pool limits are now supplied to ``create_engine`` before first use.
    """
    return db_pool_status(db)


def db_pool_status(db: Session) -> dict[str, Any]:
    engine = db.get_bind()
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {"ok": False, "reason": "engine_has_no_pool"}
    data: dict[str, Any] = {"ok": True, "pool_class": pool.__class__.__name__}
    try:
        data["status"] = pool.status()
    except Exception:
        pass
    for key, method_name in (
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
        ("size", "size"),
    ):
        try:
            data[key] = int(getattr(pool, method_name)())
        except Exception:
            pass
    data["timeout_seconds"] = getattr(pool, "_timeout", None)
    data["max_overflow"] = getattr(pool, "_max_overflow", None)
    data["recycle_seconds"] = getattr(pool, "_recycle", None)
    data["pre_ping"] = bool(getattr(pool, "_pre_ping", False))
    return data
