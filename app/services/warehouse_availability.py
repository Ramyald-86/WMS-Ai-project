"""Shared MAIN-stock locks and pick claims; physical stock remains canonical."""
from decimal import Decimal
from sqlalchemy import text


def lock_scope(db, client_id, warehouse_id):
    # Same lock as task creation in r62: transfer/close/edit cannot race an allocation.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
               {'key': f'fc:r62:wh-pick:{client_id}:{warehouse_id}'})


def held_quantity(db, client_id, warehouse_id, sku_id, location_id, container_id, exclude_task=''):
    return Decimal(str(db.execute(text('''
        SELECT COALESCE(SUM(GREATEST(l.allocated_qty,l.scan_qty+l.manual_qty+l.missing_qty)),0)
        FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
        WHERE t.client_id=:c AND t.warehouse_id=:w AND l.sku_id=:s AND l.location_id=:loc
          AND COALESCE(l.container_id,'')=:box AND t.id<>:exclude
          AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')
    '''), {'c':str(client_id),'w':str(warehouse_id),'s':str(sku_id),'loc':str(location_id),
           'box':str(container_id or ''),'exclude':str(exclude_task)}).scalar_one() or 0))


def available_buckets(db, client_id, warehouse_id):
    rows = db.execute(text('''
      SELECT CAST(sv.sku_id AS varchar) sku_id, CAST(sv.location_id AS varchar) location_id,
        NULLIF(sv.container_key,'') container_id,s.barcode,s.article,s.size,
        loc.code location_code,loc.barcode location_barcode,c.name container_name,c.barcode container_barcode,
        GREATEST(0,LEAST(sv.storage_only,b.quantity-sv.fbs_available-sv.reserved)) main,
        COALESCE(h.held,0) held
      FROM fc_stocks_v2 sv
      JOIN skus s ON CAST(s.id AS varchar)=CAST(sv.sku_id AS varchar)
      JOIN locations loc ON CAST(loc.id AS varchar)=CAST(sv.location_id AS varchar)
      LEFT JOIN containers c ON CAST(c.id AS varchar)=NULLIF(sv.container_key,'')
      JOIN inventory_balances b ON CAST(b.client_id AS varchar)=CAST(sv.client_id AS varchar)
        AND CAST(b.warehouse_id AS varchar)=CAST(sv.warehouse_id AS varchar)
        AND CAST(b.sku_id AS varchar)=CAST(sv.sku_id AS varchar)
        AND CAST(b.location_id AS varchar)=CAST(sv.location_id AS varchar)
        AND COALESCE(CAST(b.container_id AS varchar),'')=sv.container_key
      LEFT JOIN (
        SELECT l.sku_id,l.location_id,COALESCE(l.container_id,'') box,
          SUM(GREATEST(l.allocated_qty,l.scan_qty+l.manual_qty+l.missing_qty)) held
        FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
        WHERE t.client_id=:c AND t.warehouse_id=:w
          AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')
        GROUP BY l.sku_id,l.location_id,COALESCE(l.container_id,'')
      ) h ON h.sku_id=CAST(sv.sku_id AS varchar) AND h.location_id=CAST(sv.location_id AS varchar) AND h.box=sv.container_key
      WHERE sv.client_id=:c AND sv.warehouse_id=:w AND sv.storage_only>0
        AND s.is_active=TRUE AND loc.is_active=TRUE
        AND (sv.container_key='' OR (c.status='ACTIVE' AND c.location_id=loc.id))
    '''), {'c':str(client_id),'w':str(warehouse_id)}).mappings().all()
    out=[]
    for row in rows:
        b=dict(row)
        b['key']=':'.join(str(b.get(k) or '') for k in ('sku_id','location_id','container_id'))
        b['free']=max(0,int(b['main']-b['held']))
        for k in ('barcode','article','size','location_code','location_barcode','container_name','container_barcode'):
            b[k]=str(b.get(k) or '')
        out.append(b)
    return out
