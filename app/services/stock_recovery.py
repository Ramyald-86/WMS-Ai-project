"""Retry terminal writeoffs and marketplace publication without losing warehouse facts.

Only recorded terminal orders with an exact reserved source are candidates.
No balance is inferred from a free quantity. No network call runs in a stock transaction.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from sqlalchemy import text

LOG = logging.getLogger(__name__)
_thread = None
_thread_lock = threading.Lock()
_health = {'running': False, 'last_pass': None, 'last_error': None}
TERMINAL_SQL = """r.status IN ('ACTIVE','CONSUMING','PACKING_WRITE_OFF')
 AND r.source_location_id IS NOT NULL AND o.provider='WB'
 AND (o.local_status IN ('PACKED','SENT','DELIVERED')
   OR (o.local_status='CANCELLED' AND o.packed_at IS NOT NULL))"""


def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_stock_recovery_errors(
      reservation_id varchar(36) PRIMARY KEY,attempts integer NOT NULL DEFAULT 0,
      next_attempt_at timestamptz NOT NULL DEFAULT now(),last_error text,
      updated_at timestamptz NOT NULL DEFAULT now())'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_stock_publish_retry(
      job_key text PRIMARY KEY,kind varchar(16) NOT NULL,payload jsonb NOT NULL,
      attempts integer NOT NULL DEFAULT 0,next_attempt_at timestamptz NOT NULL DEFAULT now(),
      last_error text,updated_at timestamptz NOT NULL DEFAULT now())'''))


def error_text(exc):
    cause = getattr(exc, 'orig', exc)
    code = getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)
    message = getattr(exc, 'detail', None) or str(cause).splitlines()[0]
    return (f'{code}: ' if code else '') + str(message)[:1200]


def publish_now(db, kind, payload):
    if kind == 'ORDER':
        from app.models import MarketplaceOrder, SKU
        from app.services.stock_core_v2 import enqueue_marketplace_stock
        order = db.get(MarketplaceOrder, uuid.UUID(payload['order_id']))
        sku = db.get(SKU, uuid.UUID(payload['sku_id']))
        if not order or not sku:
            raise ValueError('Заказ или товар для публикации не найден')
        enqueue_marketplace_stock(db, order=order, sku=sku,
            warehouse_id=uuid.UUID(payload['warehouse_id']), reason='R71_PHYSICAL_STOCK')
        return 'Изменение WB поставлено в очередь'
    if kind == 'MANUAL':
        from app.routers.stock_actions import _queue_fbs_now
        return _queue_fbs_now(db, payload['client_id'], payload['warehouse_id'],
            payload['sku_ids'], payload.get('requested'))
    raise ValueError('Неизвестный тип публикации')


def try_publish(db, key, kind, payload):
    # SAVEPOINT keeps a failed optional projection from aborting the physical ledger.
    # The durable retry itself remains in the same transaction as that ledger.
    try:
        with db.begin_nested():
            message = publish_now(db, kind, payload)
        db.execute(text('DELETE FROM fc_stock_publish_retry WHERE job_key=:key'), {'key': key})
        return message
    except Exception as exc:
        db.execute(text('''UPDATE fc_stock_publish_retry SET attempts=attempts+1,
          next_attempt_at=now()+interval '30 seconds',last_error=:err,updated_at=now()
          WHERE job_key=:key'''), {'key': key, 'err': error_text(exc)})
        LOG.warning('Stock publication deferred key=%s error=%s', key, error_text(exc))
        return 'Остаток сохранён. Обновление WB ожидает автоматической повторной попытки'


def queue_publication(db, kind, payload):
    payload = json.loads(json.dumps(payload, default=str))
    identity = payload.get('order_id') or ','.join(sorted(payload['sku_ids']))
    key = f"{kind}:{payload['client_id']}:{payload['warehouse_id']}:{identity}"
    db.execute(text('''INSERT INTO fc_stock_publish_retry(job_key,kind,payload)
      VALUES(:key,:kind,CAST(:payload AS jsonb)) ON CONFLICT(job_key) DO UPDATE SET
      payload=EXCLUDED.payload,next_attempt_at=now(),updated_at=now()'''),
      {'key': key, 'kind': kind, 'payload': json.dumps(payload)})
    return try_publish(db, key, kind, payload)


def queue_order(db, row):
    return queue_publication(db, 'ORDER', {k: str(row[k]) for k in
        ('client_id', 'warehouse_id', 'order_id', 'sku_id')})


def report(db, limit=20):
    pending = db.execute(text('''SELECT count(*) FROM fc_stock_reservations r
      JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id
        AND CAST(o.client_id AS text)=r.client_id WHERE ''' + TERMINAL_SQL)).scalar()
    errors = db.execute(text('''SELECT e.reservation_id,e.attempts,e.last_error,e.next_attempt_at,
        r.client_id,r.warehouse_id,r.source_location_id,r.source_container_id
      FROM fc_stock_recovery_errors e JOIN fc_stock_reservations r ON r.id=e.reservation_id
      JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id AND CAST(o.client_id AS text)=r.client_id
      WHERE ''' + TERMINAL_SQL + ' ORDER BY e.updated_at DESC LIMIT :n'), {'n': limit}).mappings().all()
    return {'pending_terminal_reservations': int(pending),
        'pending_wb_publications': int(db.execute(text('SELECT count(*) FROM fc_stock_publish_retry')).scalar()),
        'errors': [dict(r) for r in errors]}


def run_batch(session_factory, limit=50):
    from app.services import stock_accounting as accounting
    result = {'processed': 0, 'changed': 0, 'failed': 0, 'publish_retried': 0}
    with session_factory() as db:
        db.execute(text("SET LOCAL statement_timeout='8s'"))
        # Errors wait independently; a broken early row cannot starve later boxes/scopes.
        rows = db.execute(text('''SELECT r.id,r.client_id,r.warehouse_id,r.order_id,r.sku_id
          FROM fc_stock_reservations r JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id
            AND CAST(o.client_id AS text)=r.client_id
          LEFT JOIN fc_stock_recovery_errors e ON e.reservation_id=r.id
          WHERE ''' + TERMINAL_SQL + ''' AND (e.next_attempt_at IS NULL OR e.next_attempt_at<=now())
          ORDER BY r.created_at,r.id LIMIT :n'''), {'n': limit}).mappings().all()
    for row in rows:
        result['processed'] += 1
        try:
            with session_factory() as db:
                db.execute(text("SET LOCAL statement_timeout='8s'"))
                done = accounting.finish(db, row['client_id'], row['warehouse_id'],
                    row['id'], 'Автоматическое списание по завершённому заказу')
                if done.get('changed'):
                    queue_order(db, row)
                db.execute(text('DELETE FROM fc_stock_recovery_errors WHERE reservation_id=:id'), {'id': row['id']})
                db.commit()
                result['changed'] += int(bool(done.get('changed')))
        except Exception as exc:
            result['failed'] += 1
            LOG.warning('Stock recovery reservation=%s error=%s', row['id'], error_text(exc))
            with session_factory() as db:
                db.execute(text('''INSERT INTO fc_stock_recovery_errors(reservation_id,attempts,next_attempt_at,last_error)
                  VALUES(:id,1,now()+interval '30 seconds',:err) ON CONFLICT(reservation_id) DO UPDATE
                  SET attempts=fc_stock_recovery_errors.attempts+1,
                  next_attempt_at=now()+interval '30 seconds',last_error=EXCLUDED.last_error,updated_at=now()'''),
                  {'id': row['id'], 'err': error_text(exc)})
                db.commit()
    with session_factory() as db:
        jobs = db.execute(text('SELECT job_key FROM fc_stock_publish_retry WHERE next_attempt_at<=now() ORDER BY next_attempt_at LIMIT :n'), {'n': limit}).scalars().all()
    for key in jobs:
        with session_factory() as db:
            db.execute(text("SET LOCAL statement_timeout='8s'"))
            job = db.execute(text('SELECT * FROM fc_stock_publish_retry WHERE job_key=:key FOR UPDATE SKIP LOCKED'), {'key': key}).mappings().first()
            if job:
                try_publish(db, key, job['kind'], job['payload'])
                db.commit()
                result['publish_retried'] += 1
    return result


def worker_status():
    return {**_health, 'running': bool(_thread and _thread.is_alive())}


def start_worker():
    global _thread
    from app.core.database import SessionLocal
    with _thread_lock:
        if _thread and _thread.is_alive():
            return False
        def run():
            pause = threading.Event()
            schema_ready = False
            while True:
                worked = False
                try:
                    if not schema_ready:
                        with SessionLocal() as db:
                            install_schema(db)
                            db.commit()
                        schema_ready = True
                    result = run_batch(SessionLocal)
                    worked = bool(result['processed'])
                    _health.update(last_pass=datetime.now(timezone.utc).isoformat(), last_error=None)
                except Exception as exc:
                    _health['last_error'] = error_text(exc)
                    LOG.exception('Stock recovery pass failed')
                pause.wait(0.1 if worked else 5)
        _thread = threading.Thread(target=run, name='fc-stock-recovery-r71', daemon=True)
        _thread.start()
        return True
