"""Commit-time warehouse accounting and scoped, transactional UI revisions."""
import uuid
from sqlalchemy import text, bindparam, event
from sqlalchemy.orm import Session

_REGISTERED=False
TERMINAL={'PACKED','SENT','DELIVERED'}

def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_stock_revisions(
      client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,
      revision bigint NOT NULL DEFAULT 1,last_tx bigint NOT NULL,
      PRIMARY KEY(client_id,warehouse_id))'''))
    db.execute(text('''CREATE OR REPLACE FUNCTION fc_r70_stock_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE a jsonb; b jsonb; fields text[]; k text; changed boolean:=false; v jsonb;
    BEGIN
      IF TG_OP<>'INSERT' THEN a:=to_jsonb(OLD); END IF;
      IF TG_OP<>'DELETE' THEN b:=to_jsonb(NEW); END IF;
      fields:=string_to_array(TG_ARGV[0],',');
      FOREACH k IN ARRAY fields LOOP
        IF a->k IS DISTINCT FROM b->k THEN changed:=true; END IF;
      END LOOP;
      IF changed THEN
        FOR v IN SELECT DISTINCT x FROM (VALUES(a),(b)) t(x)
          WHERE x->>'client_id' IS NOT NULL AND x->>'warehouse_id' IS NOT NULL
        LOOP
          INSERT INTO fc_stock_revisions(client_id,warehouse_id,last_tx)
            VALUES(v->>'client_id',v->>'warehouse_id',txid_current())
          ON CONFLICT(client_id,warehouse_id) DO UPDATE
            SET revision=fc_stock_revisions.revision+1,last_tx=EXCLUDED.last_tx
            WHERE fc_stock_revisions.last_tx<>EXCLUDED.last_tx;
        END LOOP;
      END IF;
      RETURN NULL;
    END $$'''))
    specs={
      'inventory_balances':'client_id,warehouse_id,sku_id,location_id,container_id,quantity',
      'containers':'client_id,warehouse_id,name,barcode,status,location_id',
      'fc_stocks_v2':'client_id,warehouse_id,sku_id,location_id,container_key,storage_only,fbs_available,reserved',
      'fc_stock_reservations':'client_id,warehouse_id,sku_id,source_location_id,source_container_id,quantity,status',
    }
    for table,fields in specs.items():
        # Constant identifiers only. Transactional changes and rollbacks share the revision.
        db.execute(text(f'DROP TRIGGER IF EXISTS fc_r70_revision ON {table}'))
        db.execute(text(f"CREATE TRIGGER fc_r70_revision AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION fc_r70_stock_revision('{fields}')"))

def revision(db,c,w):
    return str(db.execute(text('SELECT revision FROM fc_stock_revisions WHERE client_id=:c AND warehouse_id=:w'),{'c':str(c),'w':str(w)}).scalar() or 0)

def collect(db, *args):
    if db.info.get('r70_finishing'):return
    from app.models import MarketplaceOrder
    ids=db.info.setdefault('r70_terminal_orders',set())
    for obj in list(db.new)+list(db.dirty):
        if isinstance(obj,MarketplaceOrder) and obj.provider=='WB' and (obj.local_status in TERMINAL or obj.local_status=='CANCELLED' and obj.packed_at):
            if obj.id:ids.add(str(obj.id))

def finish_orders(db, ids):
    """Run after order flush, before its commit. Nothing is committed here."""
    if not ids:return []
    from app.services import stock_accounting as accounting
    from app.services.stock_recovery import queue_order
    from app.models import MarketplaceOrder,SKU
    rows=db.execute(text('''SELECT r.id,r.client_id,r.warehouse_id,r.order_id,r.sku_id
      FROM fc_stock_reservations r JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id AND CAST(o.client_id AS text)=r.client_id
      WHERE r.order_id IN :ids AND r.source_location_id IS NOT NULL
        AND r.status IN ('ACTIVE','CONSUMING','PACKING_WRITE_OFF') AND o.provider='WB'
        AND (o.local_status IN ('PACKED','SENT','DELIVERED') OR (o.local_status='CANCELLED' AND o.packed_at IS NOT NULL))
      ORDER BY r.client_id,r.warehouse_id,r.sku_id,r.id''').bindparams(bindparam('ids',expanding=True)),{'ids':sorted(ids)}).mappings().all()
    result=[]
    for r in rows:
        done=accounting.finish(db,r['client_id'],r['warehouse_id'],r['id'],'Списание при завершении заказа')
        if done.get('changed'):
            queue_order(db,r)
        result.append(done)
    return result

def before_commit(db):
    if db.in_nested_transaction() or db.info.get('r70_finishing'):return
    collect(db)
    ids=set(db.info.get('r70_terminal_orders',set()))
    if not ids:return
    db.info['r70_finishing']=True
    try:
        db.flush()
        finish_orders(db,ids)
        db.info['r70_terminal_orders'].difference_update(ids)
    finally:db.info['r70_finishing']=False

def transaction_end(db,transaction):
    if transaction.parent is None:
        db.info.pop('r70_terminal_orders',None)
        db.info.pop('r70_finishing',None)

def install_hooks():
    global _REGISTERED
    if _REGISTERED:return
    event.listen(Session,'before_flush',collect)
    event.listen(Session,'before_commit',before_commit)
    event.listen(Session,'after_transaction_end',transaction_end)
    _REGISTERED=True
