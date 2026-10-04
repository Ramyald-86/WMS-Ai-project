#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.2 DATABASE STABILITY"

NEW_BLOCK = '''    immediate_reservation = {"created": 0, "kept": 0, "already_consumed": 0, "shortage": 0}\n    if physical_warehouse_id:\n        # 0.9.4.2 DATABASE STABILITY:\n        # WB synchronization performs external HTTP calls. PostgreSQL is configured with\n        # idle_in_transaction_session_timeout, so a transaction left open while waiting for\n        # WB can be terminated by PostgreSQL. Never start immediate reservation on top of\n        # such a stale/aborted transaction. Close the preceding sync transaction first and,\n        # if PostgreSQL has already invalidated it, rollback so SQLAlchemy can reconnect.\n        try:\n            db.commit()\n        except Exception:\n            logger.exception("0.9.4.2 pre-reservation DB transaction was invalid; rolling back")\n            try:\n                db.rollback()\n            except Exception:\n                logger.exception("0.9.4.2 rollback after invalid pre-reservation transaction failed")\n\n        try:\n            with distributed_lock(db, f"wb-arrival-reserve:{cred.client_id}:{physical_warehouse_id}", blocking=True):\n                immediate_reservation = _reserve_new_wb_orders_immediately(\n                    db,\n                    cred=cred,\n                    physical_warehouse_id=physical_warehouse_id,\n                    selected_wb_warehouse_id=selected_wb_warehouse_id,\n                )\n            # Reservation is its own short transaction. Do not keep it open while the\n            # response performs subsequent reads or serialization.\n            db.commit()\n        except HTTPException as exc:\n            # SQLAlchemy requires rollback after any failed statement/flush before the\n            # Session can be used again. One bad reservation must not poison the WB page.\n            try:\n                db.rollback()\n            except Exception:\n                logger.exception("0.9.4.2 rollback after reservation HTTP error failed")\n            warnings.append(f"Резерв новых FBS-заказов: {exc.detail}")\n        except Exception as exc:\n            try:\n                db.rollback()\n            except Exception:\n                logger.exception("0.9.4.2 rollback after reservation failure failed")\n            logger.exception("0.9.4.2 immediate WB reservation failed safely")\n            warnings.append(f"Резерв новых FBS-заказов временно не выполнен: {exc}")\n    else:\n        db.commit()\n\n'''


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def patch_marketplaces(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    if MARKER in src:
        print("OK: marketplaces.py already contains 0.9.4.2 marker")
        return

    start, end = function_bounds(src, "wb_sync_orders")
    lines = src.splitlines(True)
    # Convert to zero-based slice positions inside wb_sync_orders only.
    block_start = None
    block_end = None
    for idx in range(start - 1, min(end, len(lines))):
        stripped = lines[idx].strip()
        if block_start is None and stripped.startswith('immediate_reservation = {'):
            block_start = idx
            continue
        if block_start is not None and stripped.startswith('counts = _wb_status_counts('):
            block_end = idx
            break

    if block_start is None or block_end is None or block_end <= block_start:
        raise RuntimeError("immediate reservation block anchors not found in wb_sync_orders")

    old = "".join(lines[block_start:block_end])
    required = ["_reserve_new_wb_orders_immediately", "distributed_lock", "immediate_reservation"]
    missing = [x for x in required if x not in old]
    if missing:
        raise RuntimeError("refusing to patch unexpected reservation block; missing: " + ", ".join(missing))

    lines[block_start:block_end] = [NEW_BLOCK]
    out = "".join(lines)
    ast.parse(out)
    if out.count(MARKER) != 1:
        raise RuntimeError("unexpected 0.9.4.2 marker count")
    path.write_text(out, encoding="utf-8")
    print(f"OK: patched {path}")


def patch_main(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    if 'version="0.9.4.2"' in src:
        print("OK: main.py already reports 0.9.4.2")
        return
    # Current stable runtime reports 0.9.4. Change only the FastAPI version literal.
    out, count = re.subn(r'version="0\.9\.4"', 'version="0.9.4.2"', src, count=1)
    if count != 1:
        raise RuntimeError("FastAPI version=\"0.9.4\" marker not found exactly once")
    ast.parse(out)
    path.write_text(out, encoding="utf-8")
    print(f"OK: patched {path} health version")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_main(root / "app/main.py")


if __name__ == "__main__":
    main()
