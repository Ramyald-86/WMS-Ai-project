#!/usr/bin/env python3
"""One-time release of CHZ bindings from already-cancelled orders."""
import sys

try:
    from app.routers import marketplaces as m
except Exception as e:
    print("STOP: cannot import app.routers.marketplaces:", repr(e))
    raise SystemExit(2)

if not hasattr(m, "MarketplaceOrder") or not hasattr(m, "get_db"):
    print("STOP: MarketplaceOrder/get_db are not available from marketplaces module")
    raise SystemExit(2)

gen = m.get_db()
db = next(gen)
released = 0
checked = 0
samples = []

def is_cancelled(order):
    values = []
    for name in ("local_status", "supplier_status", "wb_status", "status", "external_status"):
        if hasattr(order, name):
            values.append(str(getattr(order, name, "") or "").strip().upper())
    return any(v in {"CANCELLED", "CANCELED", "ОТМЕНЕН", "ОТМЕНЁН", "ОТМЕНЕННЫЙ", "ОТМЕНЁННЫЙ"} for v in values)

try:
    rows = db.query(m.MarketplaceOrder).all()
    for order in rows:
        checked += 1
        chz = str(getattr(order, "chz_code", "") or "").strip()
        if not chz or not is_cancelled(order):
            continue

        if hasattr(m, "_release_chz_on_cancel"):
            changed = bool(m._release_chz_on_cancel(db, order, getattr(order, "local_status", None)))
        else:
            order.chz_code = None
            changed = True

        if changed:
            released += 1
            if len(samples) < 10:
                samples.append(str(getattr(order, "external_order_id", None) or getattr(order, "id", "")))

    db.commit()
    print(f"OK: checked={checked} released={released}")
    if samples:
        print("SAMPLE:", ", ".join(samples))
except Exception as e:
    db.rollback()
    print("STOP: backfill failed:", repr(e))
    raise
finally:
    try:
        gen.close()
    except Exception:
        pass
