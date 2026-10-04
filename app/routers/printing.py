import base64
import asyncio
import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.models import PrintJob
from app.schemas.printing import PrintResultRequest

router = APIRouter(prefix="/printing", tags=["Печать"])

# Print Agent can poll every 150 ms. Reclaiming stale jobs is maintenance, not part of
# each hot poll. Run it at most once per 10 seconds per API worker.
_STALE_REQUEUE_LOCK = threading.Lock()
_STALE_REQUEUE_LAST_MONO = 0.0

# FC_PRINT_POLL_ASYNC_ISOLATION_09415G4_20260903
# Print Agent 1.1.8 polls every ~150 ms. Keep that hot path away from Starlette's
# shared sync-route thread pool so warehouse pages cannot be starved by printers.
# A 350 ms empty-queue cache adds at most a fraction of a second before a newly queued
# label is noticed, while cutting idle DB polls substantially.
_PRINT_POLL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fc-print-poll")
_PRINT_EMPTY_CACHE_SECONDS = 0.75
_PRINT_EMPTY_UNTIL: dict[str, float] = {}
_PRINT_ASYNC_LOCKS: dict[str, asyncio.Lock] = {}
_PRINT_STATE_LAST_SEEN: dict[str, float] = {}
_PRINT_STATE_PRUNE_LAST = 0.0
_PRINT_STATE_TTL_SECONDS = 3600.0
_PRINT_STATE_MAX_STATIONS = 128


def _prune_print_runtime_state(now: float | None = None) -> None:
    global _PRINT_STATE_PRUNE_LAST
    now = time.monotonic() if now is None else now
    if now - _PRINT_STATE_PRUNE_LAST < 60.0 and len(_PRINT_STATE_LAST_SEEN) <= _PRINT_STATE_MAX_STATIONS:
        return
    stale = [code for code, seen in _PRINT_STATE_LAST_SEEN.items() if now - seen > _PRINT_STATE_TTL_SECONDS]
    if len(_PRINT_STATE_LAST_SEEN) - len(stale) > _PRINT_STATE_MAX_STATIONS:
        keep = sorted(_PRINT_STATE_LAST_SEEN, key=_PRINT_STATE_LAST_SEEN.get, reverse=True)[:_PRINT_STATE_MAX_STATIONS]
        stale.extend(code for code in _PRINT_STATE_LAST_SEEN if code not in keep)
    for code in set(stale):
        lock = _PRINT_ASYNC_LOCKS.get(code)
        if lock is None or not lock.locked():
            _PRINT_ASYNC_LOCKS.pop(code, None)
            _PRINT_EMPTY_UNTIL.pop(code, None)
            _PRINT_STATE_LAST_SEEN.pop(code, None)
    _PRINT_STATE_PRUNE_LAST = now


def _print_station_lock(code: str) -> asyncio.Lock:
    now = time.monotonic()
    _PRINT_STATE_LAST_SEEN[code] = now
    _prune_print_runtime_state(now)
    lock = _PRINT_ASYNC_LOCKS.get(code)
    if lock is None:
        lock = asyncio.Lock()
        _PRINT_ASYNC_LOCKS[code] = lock
    return lock


def _print_empty_cached(code: str) -> bool:
    return _PRINT_EMPTY_UNTIL.get(code, 0.0) > time.monotonic()


# FC_PRINT_EMPTY_CACHE_HELPERS_09415G4A_20260903
def _set_print_empty(code: str) -> None:
    _PRINT_EMPTY_UNTIL[code] = time.monotonic() + _PRINT_EMPTY_CACHE_SECONDS


def _clear_print_empty(code: str) -> None:
    _PRINT_EMPTY_UNTIL.pop(code, None)


def _claim_next_print_job_sync(code: str) -> dict:
    db = SessionLocal()
    try:
        _maybe_requeue_stale_claims(db)
        job = db.execute(
            select(PrintJob)
            .where(PrintJob.station_code == code, PrintJob.status == "QUEUED")
            .order_by(PrintJob.created_at.asc())
            .with_for_update(skip_locked=True)
        ).scalars().first()
        if not job:
            db.rollback()
            _set_print_empty(code)
            return {"job": None}
        job.status = "CLAIMED"
        job.claimed_at = _now()
        job.attempts = (job.attempts or 0) + 1
        payload = {
            "id": str(job.id),
            "file_name": job.file_name,
            "content_type": job.content_type,
            "document_type": job.document_type,
            "content_base64": base64.b64encode(job.payload).decode("ascii"),
        }
        db.commit()
        _clear_print_empty(code)
        return {"job": payload}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass
        db.close()


def _now():
    return datetime.now(timezone.utc)


# FC_PRINT_POLL_TX_RELEASE_09415G3_20260903
def _maybe_requeue_stale_claims(db: Session) -> None:
    global _STALE_REQUEUE_LAST_MONO
    now_mono = time.monotonic()
    if now_mono - _STALE_REQUEUE_LAST_MONO < 10.0:
        return
    with _STALE_REQUEUE_LOCK:
        now_mono = time.monotonic()
        if now_mono - _STALE_REQUEUE_LAST_MONO < 10.0:
            return
        stale_before = _now() - timedelta(minutes=2)
        result = db.execute(
            update(PrintJob)
            .where(PrintJob.status == "CLAIMED", PrintJob.claimed_at < stale_before)
            .values(status="QUEUED", claimed_at=None, error_message="Повтор после потери Print Agent")
        )
        # Maintenance must be durable even when the following hot-poll SELECT finds no job.
        # Commit only once per 10 seconds; this also releases the DB connection immediately.
        if int(getattr(result, "rowcount", 0) or 0) > 0:
            db.commit()
        else:
            db.rollback()
        _STALE_REQUEUE_LAST_MONO = now_mono


@router.get("/agent/ping")
async def agent_ping(station_code: str = Query(min_length=1, max_length=64)):
    # Pure event-loop response: token checks/pings never consume a shared sync worker.
    return {"ok": True, "station_code": station_code.strip().upper(), "queue_protocol": "1.1.8-fast"}


@router.get("/agent/next")
async def next_print_job(station_code: str = Query(min_length=1, max_length=64)):
    code = station_code.strip().upper()
    if _print_empty_cached(code):
        return {"job": None}
    # FC_PRINT_POLL_NO_WAITER_QUEUE_09415G4B_20260903
    # Important: asyncio.Lock normally QUEUES every overlapping poll. Print Agents poll
    # very frequently, so a short DB slowdown used to create thousands of waiting
    # request coroutines and grow the API process to many GB. Never queue a second poll
    # for the same station: the active poll is enough; the next client poll will retry.
    lock = _print_station_lock(code)
    if lock.locked():
        return {"job": None}
    async with lock:
        if _print_empty_cached(code):
            return {"job": None}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_PRINT_POLL_EXECUTOR, _claim_next_print_job_sync, code)


@router.post("/agent/jobs/{job_id}/result")
def print_result(job_id: uuid.UUID, payload: PrintResultRequest, db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задание печати не найдено")
    job.completed_at = _now()
    if payload.success:
        job.status = "PRINTED"
        job.error_message = None
    else:
        job.status = "FAILED"
        job.error_message = (payload.error or "Ошибка печати")[:2000]
    db.commit()
    return {"ok": True, "status": job.status}


@router.post("/jobs/{job_id}/retry")
def retry_print_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задание печати не найдено")
    job.status = "QUEUED"
    job.claimed_at = None
    job.completed_at = None
    job.error_message = None
    db.commit()
    return {"ok": True}


@router.get("/jobs")
def list_print_jobs(
    station_code: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(PrintJob)
    if station_code:
        stmt = stmt.where(PrintJob.station_code == station_code.strip().upper())
    if status:
        stmt = stmt.where(PrintJob.status == status.upper())
    jobs = db.execute(stmt.order_by(PrintJob.created_at.desc()).limit(limit)).scalars().all()
    return {"jobs": [{
        "id": str(j.id), "station_code": j.station_code, "document_type": j.document_type,
        "file_name": j.file_name, "status": j.status, "attempts": j.attempts,
        "error": j.error_message, "created_at": j.created_at, "completed_at": j.completed_at,
    } for j in jobs]}
