from __future__ import annotations

import threading
import time
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.database import get_db
from app.models import MarketplaceCredential
from app.services.marketplaces import OzonClient, WBClient
from app.services.stock_core_v2 import ensure_stock_core_v2_schema

_WORKER_STARTED = False
_WORKER_GUARD = threading.Lock()


def process_stock_outbox_v2(db: Session, limit: int = 50) -> dict[str, int]:
    ensure_stock_core_v2_schema(db)
    done = failed = 0
    for _ in range(max(1, min(int(limit), 500))):
        row = db.execute(
            text(
                "SELECT * FROM fc_marketplace_stock_outbox_v2 "
                "WHERE status IN ('PENDING','FAILED') AND next_attempt_at<=CURRENT_TIMESTAMP "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
        ).mappings().first()
        if not row:
            break
        db.execute(
            text(
                "UPDATE fc_marketplace_stock_outbox_v2 SET status='PROCESSING',attempts=attempts+1,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=:id"
            ),
            {"id": row["id"]},
        )
        db.commit()
        try:
            credential = db.get(MarketplaceCredential, uuid.UUID(row["credential_id"])) if row.get("credential_id") else None
            if not credential:
                raise RuntimeError("Marketplace credential not found")
            if row["provider"] == "WB":
                if not row.get("external_warehouse_id"):
                    raise RuntimeError("WB seller warehouseId not found")
                client = WBClient(decrypt_secret(credential.encrypted_api_key))
                # WB FBS v3: seller warehouseId in URL, product barcode in sku.
                client._put(
                    f"/api/v3/stocks/{int(row['external_warehouse_id'])}",
                    {"stocks": [{"sku": str(row["product_key"]), "amount": int(row["desired_amount"])}]},
                )
            else:
                if not credential.external_client_id:
                    raise RuntimeError("Ozon Client-Id missing")
                client = OzonClient(credential.external_client_id, decrypt_secret(credential.encrypted_api_key))
                client._post(
                    "/v2/products/stocks",
                    {
                        "stocks": [
                            {
                                "offer_id": str(row["product_key"]),
                                "stock": int(row["desired_amount"]),
                                "warehouse_id": int(row["external_warehouse_id"]) if row.get("external_warehouse_id") else 0,
                            }
                        ]
                    },
                )
            db.execute(
                text(
                    "UPDATE fc_marketplace_stock_outbox_v2 SET status='DONE',last_error=NULL,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=:id"
                ),
                {"id": row["id"]},
            )
            db.commit()
            done += 1
        except Exception as exc:
            db.rollback()
            db.execute(
                text(
                    "UPDATE fc_marketplace_stock_outbox_v2 SET status='FAILED',last_error=:error,"
                    "next_attempt_at=CURRENT_TIMESTAMP+LEAST(INTERVAL '10 minutes', INTERVAL '5 seconds' * POWER(2,LEAST(attempts,7))),"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": row["id"], "error": str(exc)[:2000]},
            )
            db.commit()
            failed += 1
    return {"done": done, "failed": failed}


def _worker_loop() -> None:
    while True:
        generator = get_db()
        db = next(generator)
        try:
            process_stock_outbox_v2(db, limit=100)
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"FulfillCore Stock Core v2 outbox warning: {exc}")
        finally:
            try:
                generator.close()
            except Exception:
                pass
        time.sleep(2.0)


def start_stock_outbox_worker_v2() -> bool:
    global _WORKER_STARTED
    with _WORKER_GUARD:
        if _WORKER_STARTED:
            return False
        thread = threading.Thread(target=_worker_loop, name="fc-stock-outbox-v2", daemon=True)
        thread.start()
        _WORKER_STARTED = True
        return True
