#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.8 PLATFORM STABILITY REV2"


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def replace_function(src: str, name: str, replacement: str) -> str:
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    first = start - 1
    while first > 0 and lines[first - 1].lstrip().startswith("@"):
        first -= 1
    lines[first:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


def replace_function_keep_decorators(src: str, name: str, replacement: str) -> str:
    """Replace only the function node; keep existing decorators/routes intact."""
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    lines[start - 1:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


AUTH_FUNC = r'''@app.middleware("http")
async def single_profile_auth(request: Request, call_next):
    path = request.url.path
    public = path in {"/login", "/auth/login", "/commercial-login", "/commercial/auth/login", "/health", "/help/print-agent"} or path.startswith("/static/") or path.startswith("/downloads/print-agent/")
    if public:
        return await call_next(request)

    if path.startswith("/printing/agent/"):
        supplied = request.headers.get("x-print-agent-token", "")
        header_station_raw = request.headers.get("x-print-agent-station") or ""
        query_station_raw = request.query_params.get("station_code") or ""
        header_station = normalize_station_code(header_station_raw) if header_station_raw else None
        query_station = normalize_station_code(query_station_raw) if query_station_raw else None
        if header_station and query_station and header_station != query_station:
            return JSONResponse({"detail": "PACK-код токена не совпадает с запрошенным рабочим местом"}, status_code=403)
        station = header_station or query_station or ""

        if not station and path.startswith("/printing/agent/jobs/") and path.endswith("/result"):
            try:
                import uuid as _uuid
                from app.models.printing import PrintJob as _PrintJob
                _job_id = path.rstrip("/").split("/")[-2]
                _gen = get_db()
                _db = next(_gen)
                try:
                    _job = _db.get(_PrintJob, _uuid.UUID(_job_id))
                    if _job and _job.station_code:
                        station = normalize_station_code(_job.station_code)
                finally:
                    _gen.close()
            except Exception:
                station = ""

        legacy_ok = bool(settings.print_agent_token and secrets.compare_digest(supplied, settings.print_agent_token))
        station_ok = bool(station and verify_station_token(settings.print_agent_token, station, supplied))
        if legacy_ok or station_ok:
            request.state.print_agent_station = station
            return await call_next(request)
        return JSONResponse({"detail": "Неверный токен Print Agent или код рабочего места"}, status_code=401)

    session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
    if session and session.get("u") == settings.admin_username:
        request.state.username = session["u"]
        request.state.super_admin = True
        return await call_next(request)

    # REV2: commercial auth owns a DB connection only for the short auth lookup.
    commercial = None
    generator = get_db()
    db = next(generator)
    try:
        raw_commercial = commercial_session_from_request(request, db)
        if raw_commercial:
            commercial = {
                "username": raw_commercial.get("username"),
                "tenant_id": raw_commercial.get("tenant_id"),
                "blocked": bool(raw_commercial.get("blocked")),
                "allowed_client_ids": list(raw_commercial.get("allowed_client_ids") or []),
                "allowed_warehouse_ids": list(raw_commercial.get("allowed_warehouse_ids") or []),
            }
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass
    finally:
        try: generator.close()
        except Exception: pass

    if commercial and not commercial.get("blocked"):
        request.state.username = commercial["username"]
        request.state.tenant_id = commercial["tenant_id"]
        request.state.super_admin = False
        request.state.allowed_client_ids = set(commercial.get("allowed_client_ids") or [])
        request.state.allowed_warehouse_ids = set(commercial.get("allowed_warehouse_ids") or [])

        query_client = request.query_params.get("client_id")
        query_wh = request.query_params.get("warehouse_id")
        if query_client and query_client not in request.state.allowed_client_ids:
            return JSONResponse({"detail": "Нет доступа к этому клиенту"}, status_code=403)
        if query_wh and query_wh not in request.state.allowed_warehouse_ids:
            return JSONResponse({"detail": "Нет доступа к этому складу"}, status_code=403)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            raw = await request.body()
            text_body = raw.decode("utf-8", "ignore")
            for key, allowed in (("client_id", request.state.allowed_client_ids), ("warehouse_id", request.state.allowed_warehouse_ids)):
                found = []
                try:
                    if request.headers.get("content-type", "").startswith("application/json"):
                        obj = json.loads(text_body or "{}")
                        value = obj.get(key) if isinstance(obj, dict) else None
                        if value:
                            found = [str(value)]
                    else:
                        found = re.findall(r'name="' + key + r'"\r\n\r\n([^\r\n]+)', text_body)
                except Exception:
                    found = []
                if found and any(str(value) not in allowed for value in found):
                    return JSONResponse({"detail": f"Нет доступа: {key}"}, status_code=403)

        # No auth DB session is alive while the actual endpoint runs.
        response = await call_next(request)

        if request.method == "POST" and path in {"/masterdata/clients", "/masterdata/warehouses"} and response.status_code < 300:
            body = b""
            try:
                body = b"".join([chunk async for chunk in response.body_iterator])
            except Exception as exc:
                print(f"FulfillCore tenant response read warning: {exc}")
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            rebuilt = Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                resource_id = str(data.get("id") or "") if isinstance(data, dict) else ""
                if resource_id:
                    gen2 = get_db()
                    db2 = next(gen2)
                    try:
                        link_resource_to_tenant(
                            db2,
                            commercial["tenant_id"],
                            "client" if path.endswith("/clients") else "warehouse",
                            resource_id,
                        )
                        db2.commit()
                    except Exception as exc:
                        try: db2.rollback()
                        except Exception: pass
                        print(f"FulfillCore tenant link warning: {exc}")
                    finally:
                        try: gen2.close()
                        except Exception: pass
            except Exception as exc:
                print(f"FulfillCore tenant response parse warning: {exc}")
            return rebuilt
        return response

    if commercial and commercial.get("blocked"):
        wants_html = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if wants_html:
            return HTMLResponse("<h2>Подписка FulfillCore закончилась или кабинет отключён.</h2><p>Обратитесь к владельцу программы.</p>", status_code=403)
        return JSONResponse({"detail": "Подписка закончилась или кабинет отключён"}, status_code=403)

    wants_html = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if wants_html:
        return RedirectResponse(url="/login", status_code=303)
    return JSONResponse({"detail": "Требуется вход"}, status_code=401)
'''


STARTUP_FUNC = r'''@app.on_event("startup")
def reliability_startup():
    """Run reliability steps independently so one failure cannot hide the rest."""
    def run_db_step(name, fn):
        generator = get_db()
        db = next(generator)
        try:
            result = fn(db)
            print(f"FulfillCore startup {name}: {result if result is not None else 'OK'}")
            return result
        except Exception as exc:
            try: db.rollback()
            except Exception: pass
            print(f"FulfillCore startup {name} WARNING: {exc}")
            return None
        finally:
            try: generator.close()
            except Exception: pass

    run_db_step("db_pool", tune_db_pool)
    run_db_step("commercial_schema", ensure_commercial_schema)
    run_db_step("runtime_schema", install_runtime_schema)
    run_db_step("stock_reservation_schema", install_stock_reservation_schema)
    run_db_step("stock_core_v2_schema", install_stock_core_v2_schema)
    try:
        start_stock_outbox_worker_v2()
        print("FulfillCore startup stock_outbox_worker: OK")
    except Exception as exc:
        print(f"FulfillCore startup stock_outbox_worker WARNING: {exc}")

    result = run_db_step("reservation_backfill", backfill_legacy_reservations)
    if isinstance(result, dict) and result.get("created"):
        print(f"FulfillCore stock reservation backfill: {result}")
'''


DB_POOL_TUNING = r'''from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings


def db_pool_status(db: Session) -> dict[str, Any]:
    """Read QueuePool state without mutating SQLAlchemy private attributes."""
    engine = db.get_bind()
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {"ok": False, "reason": "engine_has_no_pool"}
    data: dict[str, Any] = {
        "ok": True,
        "pool_class": pool.__class__.__name__,
        "max_overflow": int(settings.db_max_overflow),
        "timeout_seconds": int(settings.db_pool_timeout),
        "recycle_seconds": int(settings.db_pool_recycle),
        "pre_ping": True,
    }
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
    data["pool_size"] = int(data.get("size", settings.db_pool_size))
    return data


def tune_db_pool(db: Session) -> dict[str, Any]:
    """Compatibility shim: pool is configured once in create_engine, never patched."""
    data = db_pool_status(db)
    data["configured_in_create_engine"] = True
    return data
'''


DISTRIBUTED_LOCK = r'''@contextlib.contextmanager
def distributed_lock(
    db: Session,
    key: str,
    *,
    blocking: bool = True,
    timeout_seconds: float = 5.0,
) -> Iterator[bool]:
    """Bounded advisory lock; no request may wait forever behind PostgreSQL."""
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    lock_key = f"fulfillcore:{key}"
    timeout = max(0.1, min(float(timeout_seconds), 5.0))
    if dialect == "postgresql":
        engine = getattr(bind, "engine", bind)
        with engine.connect() as lock_conn:
            acquired = False
            if blocking:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    acquired = bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": lock_key}).scalar())
                    if acquired:
                        break
                    time.sleep(0.05)
                if not acquired:
                    raise TimeoutError(f"FulfillCore lock timeout after {timeout:.1f}s: {key}")
            else:
                acquired = bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": lock_key}).scalar())
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": lock_key})
                    except Exception:
                        pass
        return

    lock = _process_lock(lock_key)
    acquired = lock.acquire(timeout=timeout) if blocking else lock.acquire(blocking=False)
    if blocking and not acquired:
        raise TimeoutError(f"FulfillCore process lock timeout after {timeout:.1f}s: {key}")
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
'''


def patch_config(src: str) -> str:
    if 'db_pool_size:' in src and 'db_max_overflow:' in src:
        return src
    tree = ast.parse(src)
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Settings'), None)
    if cls is None:
        raise RuntimeError('Settings class not found in config.py')
    lines = src.splitlines(True)
    insert_after = None
    for n in cls.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'database_url':
            insert_after = n.end_lineno or n.lineno
            break
    if insert_after is None:
        insert_after = cls.body[0].lineno - 1 if cls.body else cls.lineno
    if 'from pydantic import Field' not in src:
        lines0 = src.splitlines(True)
        insert_at = 0
        while insert_at < len(lines0) and (lines0[insert_at].startswith('from __future__') or not lines0[insert_at].strip()):
            insert_at += 1
        lines0.insert(insert_at, 'from pydantic import Field\n')
        src = ''.join(lines0)
        tree = ast.parse(src)
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Settings'), None)
        lines = src.splitlines(True)
        insert_after = None
        for n in cls.body:
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'database_url':
                insert_after = n.end_lineno or n.lineno
                break
        if insert_after is None:
            insert_after = cls.body[0].lineno - 1 if cls.body else cls.lineno
    block = (
        '    # 0.9.4.8: QueuePool is configured by validated Settings, not private pool mutation.\n'
        '    db_pool_size: int = Field(default=20, validation_alias="FC_DB_POOL_SIZE")\n'
        '    db_max_overflow: int = Field(default=20, validation_alias="FC_DB_MAX_OVERFLOW")\n'
        '    db_pool_timeout: int = Field(default=5, validation_alias="FC_DB_POOL_TIMEOUT")\n'
        '    db_pool_recycle: int = Field(default=900, validation_alias="FC_DB_POOL_RECYCLE")\n'
    )
    lines[insert_after:insert_after] = [block]
    out = ''.join(lines)
    ast.parse(out)
    return out


def patch_database(src: str) -> str:
    if 'pool_size=settings.db_pool_size' in src:
        return src
    pattern = re.compile(r'engine\s*=\s*create_engine\(\s*settings\.database_url\s*,\s*pool_pre_ping\s*=\s*True\s*\)')
    repl = '''_engine_kwargs = {"pool_pre_ping": True}\nif not str(settings.database_url).lower().startswith("sqlite"):\n    _engine_kwargs.update({\n        "pool_size": settings.db_pool_size,\n        "max_overflow": settings.db_max_overflow,\n        "pool_timeout": settings.db_pool_timeout,\n        "pool_recycle": settings.db_pool_recycle,\n    })\nengine = create_engine(settings.database_url, **_engine_kwargs)'''
    out, count = pattern.subn(repl, src, count=1)
    if count != 1:
        raise RuntimeError('create_engine(settings.database_url, pool_pre_ping=True) not found')
    ast.parse(out)
    return out


def patch_main(src: str) -> str:
    if 'from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response' not in src:
        src = src.replace(
            'from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse',
            'from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response',
            1,
        )
    src = replace_function(src, 'single_profile_auth', AUTH_FUNC)
    src = replace_function(src, 'reliability_startup', STARTUP_FUNC)
    # The production workstation agent deliberately stays on the proven 1.1.7 SAFE SIMPLE line.
    src = src.replace('Print Agent 1.2.0 with profiles DPI and durable print journal', 'Print Agent 1.1.7 SAFE SIMPLE with direct TSPL printing and duplicate-print protection')
    src = src.replace('Print Agent 1.2.0 с профилями DPI и устойчивым журналом печати', 'Print Agent 1.1.7 SAFE SIMPLE с прямой TSPL-печатью и защитой от повторной печати')
    if MARKER not in src:
        insert = 'BASE_DIR = Path(__file__).resolve().parent\n'
        src = src.replace(insert, f'# {MARKER}\n' + insert, 1)
    ast.parse(src)
    return src


def patch_printing(src: str) -> str:
    # Request is needed to verify that the ACK comes from the authenticated PACK station.
    if not re.search(r'\bRequest\b', src):
        insert_at = 0
        lines0 = src.splitlines(True)
        while insert_at < len(lines0) and (lines0[insert_at].startswith('from __future__') or not lines0[insert_at].strip()):
            insert_at += 1
        lines0.insert(insert_at, 'from fastapi import Request\n')
        src = ''.join(lines0)
    # Find only the queue-poll function, then make the lock query bounded to one id row.
    start, end = function_bounds(src, 'next_print_job')
    lines = src.splitlines(True)
    segment = ''.join(lines[start - 1:end])
    pattern = re.compile(
        r'(?P<indent>^[ \t]*)job\s*=\s*db\.execute\(\s*select\(PrintJob\)(?P<body>.*?)\)\.scalars\(\)\.first\(\)',
        re.M | re.S,
    )
    m = pattern.search(segment)
    if not m:
        raise RuntimeError('next_print_job select(PrintJob)...scalars().first() not found')
    body = m.group('body')
    if '.limit(1)' not in body:
        body = body.replace('.with_for_update(skip_locked=True)', '.limit(1)\n        .with_for_update(skip_locked=True)', 1)
    repl = (
        f"{m.group('indent')}job_id = db.execute(\n"
        f"        select(PrintJob.id){body}\n"
        f"    ).scalar_one_or_none()\n"
        f"{m.group('indent')}job = db.get(PrintJob, job_id) if job_id else None"
    )
    segment = segment[:m.start()] + repl + segment[m.end():]

    # If a reclaim UPDATE precedes the select, commit it before opening the claim transaction.
    if 'CLAIMED' in segment and 'db.commit()  # REV2 reclaim commit' not in segment:
        idx = segment.find('db.execute(')
        select_idx = segment.find('job_id = db.execute(')
        if 0 <= idx < select_idx:
            depth = 0
            pos = idx + len('db.execute')
            open_idx = segment.find('(', pos)
            if open_idx >= 0:
                depth = 1
                j = open_idx + 1
                in_str = None
                escape = False
                while j < len(segment) and depth:
                    ch = segment[j]
                    if in_str:
                        if escape:
                            escape = False
                        elif ch == '\\':
                            escape = True
                        elif ch == in_str:
                            in_str = None
                    else:
                        if ch in {'"', "'"}:
                            in_str = ch
                        elif ch == '(':
                            depth += 1
                        elif ch == ')':
                            depth -= 1
                    j += 1
                if depth == 0 and j < select_idx:
                    segment = segment[:j] + '\n    db.commit()  # REV2 reclaim commit' + segment[j:]

    # Agent result may only update a job belonging to its authenticated station.
    if 'def print_result' in src:
        pstart, pend = function_bounds(src, 'print_result')
        pseg = ''.join(src.splitlines(True)[pstart - 1:pend])
        if 'REV2 station ownership' not in pseg and 'db.get(PrintJob' in pseg:
            signature_end = pseg.find('):')
            if signature_end < 0:
                signature_end = pseg.find(') ->')
            signature = pseg[:signature_end + 1] if signature_end >= 0 else pseg.split('\n', 1)[0]
            if not re.search(r'\brequest\s*:', signature):
                pseg = pseg.replace('def print_result(', 'def print_result(request: Request, ', 1)
            get_match = re.search(r'(?m)^(?P<indent>[ \t]*)job\s*=\s*db\.get\(PrintJob[^\n]*\)\s*$', pseg)
            if get_match:
                indent = get_match.group('indent')
                ins = (
                    '\n' + indent + '# REV2 station ownership: one PACK agent cannot ACK another PACK queue.\n'
                    + indent + 'if job is not None:\n'
                    + indent + '    auth_station = str(getattr(request.state, "print_agent_station", "") or "").strip().upper()\n'
                    + indent + '    job_station = str(getattr(job, "station_code", "") or "").strip().upper()\n'
                    + indent + '    if auth_station and job_station and auth_station != job_station:\n'
                    + indent + '        raise HTTPException(status_code=403, detail="Print Agent не может подтвердить задание другого рабочего места")'
                )
                pseg = pseg[:get_match.end()] + ins + pseg[get_match.end():]
                src = replace_function_keep_decorators(src, 'print_result', pseg)

    # Replace next_print_job after print_result replacement so line numbers are current.
    src = replace_function_keep_decorators(src, 'next_print_job', segment)
    ast.parse(src)
    return src


def patch_runtime(src: str) -> str:
    return replace_function(src, 'distributed_lock', DISTRIBUTED_LOCK)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    args = ap.parse_args()
    root = Path(args.root)

    files = {
        'main': root / 'app/main.py',
        'config': root / 'app/core/config.py',
        'database': root / 'app/core/database.py',
        'pool': root / 'app/services/db_pool_tuning.py',
        'printing': root / 'app/routers/printing.py',
        'runtime': root / 'app/services/runtime_reliability.py',
    }
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        raise RuntimeError('required live files missing: ' + ', '.join(missing))

    main_src = files['main'].read_text(encoding='utf-8')
    config_src = files['config'].read_text(encoding='utf-8')
    database_src = files['database'].read_text(encoding='utf-8')
    printing_src = files['printing'].read_text(encoding='utf-8')
    runtime_src = files['runtime'].read_text(encoding='utf-8')

    if MARKER in main_src and 'configured_in_create_engine' in files['pool'].read_text(encoding='utf-8'):
        # Idempotent maintenance: keep API metadata aligned with the approved 1.1.7 agent.
        maintained = main_src.replace('Print Agent 1.2.0 with profiles DPI and durable print journal', 'Print Agent 1.1.7 SAFE SIMPLE with direct TSPL printing and duplicate-print protection')
        maintained = maintained.replace('Print Agent 1.2.0 с профилями DPI и устойчивым журналом печати', 'Print Agent 1.1.7 SAFE SIMPLE с прямой TSPL-печатью и защитой от повторной печати')
        if maintained != main_src:
            files['main'].write_text(maintained, encoding='utf-8')
        print('Already patched platform 0.9.4.8 REV2')
        return

    main_src = patch_main(main_src)
    config_src = patch_config(config_src)
    database_src = patch_database(database_src)
    printing_src = patch_printing(printing_src)
    runtime_src = patch_runtime(runtime_src)

    files['main'].write_text(main_src, encoding='utf-8')
    files['config'].write_text(config_src, encoding='utf-8')
    files['database'].write_text(database_src, encoding='utf-8')
    files['pool'].write_text(DB_POOL_TUNING, encoding='utf-8')
    files['printing'].write_text(printing_src, encoding='utf-8')
    files['runtime'].write_text(runtime_src, encoding='utf-8')

    for p in files.values():
        ast.parse(p.read_text(encoding='utf-8'))
    print('OK: 0.9.4.8 REV2 platform stability applied')


if __name__ == '__main__':
    main()
