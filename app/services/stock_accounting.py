"""Terminal orders consume their source exactly once, including legacy reservations."""
import json
import logging
import threading
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text,bindparam
from app.services import stock_actions as actions

LOG=logging.getLogger(__name__)


def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_stock_accounting_audit (
      reservation_id varchar(36) PRIMARY KEY,client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,
      order_id varchar(36) NOT NULL,operation_id varchar(36),quantity numeric NOT NULL,
      method varchar(32) NOT NULL,actor text,detail jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now())'''))


def candidates(db,c,w,sku_ids=None,limit=200,automatic=False):
    where=''
    params={'c':str(c),'w':str(w),'n':limit}
    if sku_ids:
        where+=' AND r.sku_id IN :ids'
        params['ids']=[str(s) for s in sku_ids]
    query=text('''SELECT r.*,o.local_status,o.packed_at,
        COALESCE(CAST(o.external_order_num AS text),o.external_order_id) order_label,s.article,s.size,s.barcode,
        l.code location,COALESCE(b.name,b.barcode) container
      FROM fc_stock_reservations r JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id
        AND CAST(o.client_id AS text)=r.client_id
      JOIN skus s ON CAST(s.id AS text)=r.sku_id AND CAST(s.client_id AS text)=r.client_id
      LEFT JOIN locations l ON CAST(l.id AS text)=r.source_location_id AND CAST(l.warehouse_id AS text)=r.warehouse_id
      LEFT JOIN containers b ON CAST(b.id AS text)=r.source_container_id AND CAST(b.client_id AS text)=r.client_id
      WHERE r.client_id=:c AND r.warehouse_id=:w
        AND r.status IN ('ACTIVE','CONSUMING','PACKING_WRITE_OFF')
        AND (o.local_status IN ('PACKED','SENT','DELIVERED') OR (o.local_status='CANCELLED' AND o.packed_at IS NOT NULL)) AND o.provider='WB' AND r.source_location_id IS NOT NULL
      '''+where+' ORDER BY r.created_at,r.id LIMIT :n')
    if sku_ids:query=query.bindparams(bindparam('ids',expanding=True))
    return [dict(r) for r in db.execute(query,params).mappings().all()]


def evidence(db,r):
    key='stock-core-v2-pack-'+r['order_id']+'-'+r['id']
    other='pack-unallocated-'+r['order_id']
    rows=db.execute(text('''SELECT CAST(o.id AS text) id,o.status,o.idempotency_key,
          COALESCE(SUM(CASE WHEN CAST(l.sku_id AS text)=:s AND l.destination_location_id IS NULL
            AND l.source_location_id IS NOT NULL AND (o.idempotency_key IN (:key,:other) OR (CAST(l.source_location_id AS text)=:loc AND COALESCE(CAST(l.source_container_id AS text),'')=:box)) THEN l.quantity ELSE 0 END),0) quantity
        FROM inventory_operations o LEFT JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
          AND (o.idempotency_key IN (:key,:other) OR (o.operation_type='SHIPPING' AND o.reference=:ref AND :ref<>'FBS ')) GROUP BY o.id,o.status,o.idempotency_key'''),
        {'c':r['client_id'],'w':r['warehouse_id'],'s':r['sku_id'],'key':key,'other':other,'loc':r.get('source_location_id') or '', 'box':r.get('source_container_id') or '', 'ref':'FBS '+str(r.get('order_label') or '')}).mappings().all()
    for op in rows:
        if (op['status'] or 'COMPLETED')=='COMPLETED' and actions.number(op['quantity'])>=actions.number(r['quantity']):
            return dict(op)
    if rows:raise HTTPException(409,detail='У заказа есть отменённое или неполное списание. Проверьте его историю')
    return None


def lock_reservation(db,c,w,rid):
    r=db.execute(text('SELECT order_id FROM fc_stock_reservations WHERE id=:id AND client_id=:c AND warehouse_id=:w'),
                 {'id':str(rid),'c':str(c),'w':str(w)}).mappings().first()
    if not r:raise HTTPException(404,detail='Резерв не найден')
    # Matches the existing session-level packing/cancellation advisory lock.
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'fulfillcore:stock-reservation-order:'+r['order_id']})
    db.execute(text('SELECT id FROM marketplace_orders WHERE CAST(id AS text)=:id FOR UPDATE'),{'id':r['order_id']})
    row=db.execute(text('''SELECT r.*,o.local_status,o.packed_at,COALESCE(CAST(o.external_order_num AS text),o.external_order_id) order_label FROM fc_stock_reservations r
      JOIN marketplace_orders o ON CAST(o.id AS text)=r.order_id AND CAST(o.client_id AS text)=r.client_id
      WHERE r.id=:id AND r.client_id=:c AND r.warehouse_id=:w AND o.provider='WB' FOR UPDATE OF r'''),
      {'id':str(rid),'c':str(c),'w':str(w)}).mappings().first()
    return dict(row) if row else None


def inspect(db,r):
    op=evidence(db,r);b=actions.bucket(db,r['client_id'],r['warehouse_id'],r['sku_id'],r['source_location_id'],r.get('source_container_id'))
    return {'reservation_id':r['id'],'order_id':r['order_id'],'status':r['status'],
            'local_status':r['local_status'],'quantity':str(r['quantity']),
            'source':b,'operation_id':op['id'] if op else None,
            'legacy':r.get('reservation_mode')!='SALE_FIRST_V1'}


def shipping(db,r,actor):
    from app.schemas.inventory import OperationCreate,StockLine
    from app.services.inventory import execute_operation
    op=execute_operation(db,OperationCreate(client_id=uuid.UUID(r['client_id']),warehouse_id=uuid.UUID(r['warehouse_id']),
       operation_type='SHIPPING',idempotency_key='stock-core-v2-pack-'+r['order_id']+'-'+r['id'],performed_by=actor,
       reference='FBS '+str(r.get('order_label') or r['order_id']),
       comment='Завершение списания по упакованному / отгруженному заказу. Исходный адрес сохранён в истории.',
       lines=[StockLine(sku_id=uuid.UUID(r['sku_id']),quantity=actions.number(r['quantity']),
         source_location_id=uuid.UUID(r['source_location_id']),source_container_id=uuid.UUID(r['source_container_id']) if r.get('source_container_id') else None,
         reason='R70_TERMINAL_ORDER_WRITE_OFF')]),commit=False)
    return str(op.id)


def finish(db,c,w,rid,actor,allow_legacy=True,expected=None):
    from app.services.stock_core_v2 import ensure_stock_core_v2_schema
    ensure_stock_core_v2_schema(db)
    r=lock_reservation(db,c,w,rid)
    if not r:raise HTTPException(404,detail='Заказ не найден')
    if r['status']=='CONSUMED':return {'changed':False,'reservation_id':str(rid)}
    if r['status'] not in ('ACTIVE','CONSUMING','PACKING_WRITE_OFF') or not (r['local_status'] in ('PACKED','SENT','DELIVERED') or r['local_status']=='CANCELLED' and r.get('packed_at')):
        raise HTTPException(409,detail='Статус заказа изменился. Повторите проверку')
    actions.locks(db,c,w,[r['sku_id']]);snapshot=inspect(db,r)
    if expected and actions.fingerprint(snapshot)!=expected:raise HTTPException(409,detail='Адрес, остаток или заказ изменился. Проверьте списание заново')
    op=snapshot['operation_id'];b=snapshot['source'];qty=actions.number(r['quantity'])
    if not op:
        # PACKED/SENT is the recorded warehouse fact; no second operator confirmation.
        if qty<=0 or qty!=qty.to_integral_value():raise HTTPException(409,detail='Некорректное количество резерва')
        # Never use the MAIN class, another address or another order's units.
        other=max(Decimal(0),b['fbs_held']-qty)
        available=max(Decimal(0),b['physical']-b['storage_raw']-other)
        actual=min(qty,available)
        if actual!=actual.to_integral_value():raise HTTPException(409,detail='Некорректный дробный остаток на исходном адресе')
        snapshot['written_off_quantity']=str(actual)
        snapshot['shortage_quantity']=str(qty-actual)
        if actual:actions.place(db,c,w,r['source_location_id'],r.get('source_container_id'))
        db.execute(text("UPDATE fc_stock_reservations SET status='PACKING_WRITE_OFF',updated_at=now() WHERE id=:id"),{'id':str(rid)})
        op=shipping(db,dict(r,quantity=actual),actor) if actual else None
        after=actions.bucket(db,c,w,r['sku_id'],r['source_location_id'],r.get('source_container_id'))
        if after['physical']!=b['physical']-actual:raise HTTPException(409,detail='Повторное или неполное списание остановлено')
        method='TERMINAL_SOURCE_SHORTAGE' if actual<qty else 'TERMINAL_WRITE_OFF'
        physical=b['physical']-actual
    else:
        method='ALREADY_WRITTEN_OFF';physical=b['physical']
    db.execute(text("UPDATE fc_stock_reservations SET status='CONSUMED',consumed_at=COALESCE(consumed_at,now()),updated_at=now(),note=:note WHERE id=:id"),
               {'id':str(rid),'note':'R70_'+method+'; '+str(r.get('note') or '')[:300]})
    # Rebuild only the technical mirror; the original SHIPPING operation remains untouched.
    # Re-read live reservations after closing this one, including legacy reference evidence.
    b['reserved']=actions.bucket(db,c,w,r['sku_id'],r['source_location_id'],r.get('source_container_id'))['reserved']
    actions.save_mirror(db,c,w,b,physical,b['storage_raw'])
    db.execute(text("UPDATE fc_orders_v2 SET status='WRITTEN_OFF',written_off_at=COALESCE(written_off_at,now()),scan_completed_at=COALESCE(scan_completed_at,now()) WHERE source_order_id=:id AND client_id=:c AND warehouse_id=:w"),
               {'id':r['order_id'],'c':str(c),'w':str(w)})
    db.execute(text('''INSERT INTO fc_stock_accounting_audit(reservation_id,client_id,warehouse_id,order_id,operation_id,quantity,method,actor,detail)
      VALUES(:id,:c,:w,:o,:op,:qty,:method,:actor,CAST(:detail AS jsonb)) ON CONFLICT(reservation_id) DO NOTHING'''),
      {'id':str(rid),'c':str(c),'w':str(w),'o':r['order_id'],'op':op,'qty':qty,'method':method,'actor':actor,'detail':json.dumps(snapshot,default=str)})
    return {'changed':True,'reservation_id':str(rid),'operation_id':op,'method':method,'quantity':float(b['physical']-physical), 'shortage':float(snapshot.get('shortage_quantity',0))}


def preview(db,c,w,ids,actor):
    rows=candidates(db,c,w,ids);result=[];snapshots=[]
    for r in rows:
        s=inspect(db,r);snapshots.append(s)
        result.append({k:r.get(k) for k in ('id','order_label','article','size','barcode','location','container','quantity')} | {'already_written_off':bool(s['operation_id']),'legacy':s['legacy']})
    if not rows:return {'rows':[],'quantity':0}
    pid=str(uuid.uuid4());digest=actions.fingerprint(snapshots)
    db.execute(text('''INSERT INTO fc_stock_action_plans(id,client_id,warehouse_id,request_json,preview_json,fingerprint,actor)
      VALUES(:id,:c,:w,CAST(:req AS jsonb),CAST(:p AS jsonb),:fp,:actor)'''),
      {'id':pid,'c':str(c),'w':str(w),'req':json.dumps({'action':'ACCOUNTING','ids':[r['id'] for r in rows]}),
       'p':json.dumps(snapshots,default=str),'fp':digest,'actor':actor})
    places={}
    for r,s in zip(rows,snapshots):
        if s['operation_id']:continue
        b=s['source'];key=(r['sku_id'],r['source_location_id'],r.get('source_container_id') or '')
        if key not in places:places[key]={'barcode':r['barcode'],'location':r['location'],'container':r['container'],'before':float(b['physical']),'remove':0}
        places[key]['remove']+=float(r['quantity'])
    return {'id':pid,'fingerprint':digest,'rows':result,'places':[dict(v,after=v['before']-v['remove']) for v in places.values()],
            'quantity':sum(float(r['quantity']) for r in result if not r['already_written_off'])}


def confirm(db,c,w,pid,digest,actor,confirmed_missing):
    from app.services.stock_core_v2 import ensure_stock_core_v2_schema
    ensure_stock_core_v2_schema(db)
    p=db.execute(text('SELECT * FROM fc_stock_action_plans WHERE id=:id AND client_id=:c AND warehouse_id=:w FOR UPDATE'),
       {'id':str(pid),'c':str(c),'w':str(w)}).mappings().first()
    if not p or p['request_json'].get('action')!='ACCOUNTING':raise HTTPException(404,detail='План списаний не найден')
    if p['fingerprint']!=digest:raise HTTPException(409,detail='Проверьте списания заново')
    if p['status']=='POSTED':return {'ok':True,'replayed':True}
    snapshots=p['preview_json']
    # Validate every source before the first movement. Several orders may share a bucket.
    for s in snapshots:
        r=lock_reservation(db,c,w,s['reservation_id'])
        if not r:raise HTTPException(409,detail='Заказ или резерв изменился')
        actions.locks(db,c,w,[r['sku_id']])
        if actions.fingerprint(inspect(db,r))!=actions.fingerprint(s):
            raise HTTPException(409,detail='Остаток или заказ изменился. Проверьте списания заново')
    results=[finish(db,c,w,s['reservation_id'],actor,allow_legacy=True) for s in snapshots]
    db.execute(text("UPDATE fc_stock_action_plans SET status='POSTED',confirmed_at=now() WHERE id=:id"),{'id':str(pid)})
    return {'ok':True,'results':results}


def start_worker():
    # Compatibility entry point; independent startup owns the retry worker in r71.
    from app.services.stock_recovery import start_worker as start
    return start()
