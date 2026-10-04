#!/usr/bin/env python3
from pathlib import Path
import ast

p = Path("app/routers/marketplaces.py")
if not p.exists():
    raise SystemExit("STOP: app/routers/marketplaces.py not found")
s = p.read_text(encoding="utf-8")
ast.parse(s)
need = [
    "def _release_chz_on_cancel",
    '"CHZ_RELEASED_ON_CANCEL"',
    "_release_chz_on_cancel(db, order, previous_status)",
    "def _move_cancelled_picked_item_to_unplaced",
]
missing = [x for x in need if x not in s]
if missing:
    raise SystemExit("STOP: missing markers: " + ", ".join(missing))
print("OK: FulfillCore 0.9.4.1 CHZ cancel-release markers present")
