"""Receipt history and atomic MAIN -> FBS allocation. No marketplace I/O in writes."""
import json
import re
import uuid
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import bindparam, text, select
from app.models import InventoryOperation, SKU
from app.services import stock_actions as stock


def install_schema(db,migrate_r79=True):
    from app.services.wb_sale_status import install_schema as sale_schema
    sale_schema(db)
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_wh_receipt_fbs (
        line_id varchar(36) PRIMARY KEY,quantity numeric(18,3) NOT NULL,
        reloadable_quantity numeric(18,3) NOT NULL DEFAULT 0,
        actor varchar(120),updated_at timestamptz NOT NULL DEFAULT now())'''))
    db.execute(text('''ALTER TABLE fc_wh_receipt_fbs
        ADD COLUMN IF NOT EXISTS reloadable_quantity numeric(18,3) NOT NULL DEFAULT 0'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_receipt_fbs_events (
        id varchar(36) PRIMARY KEY,client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,
        operation_id varchar(36) NOT NULL,request_hash varchar(64) NOT NULL,
        result_json jsonb NOT NULL,actor varchar(120),created_at timestamptz NOT NULL DEFAULT now())'''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_receipt_fbs_events_scope ON fc_receipt_fbs_events(client_id,warehouse_id,operation_id,created_at)'))
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_receipt_fbs_reset_releases (
        reset_line_id varchar(36) NOT NULL,
        receipt_line_id varchar(36) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(reset_line_id,receipt_line_id))'''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_receipt_fbs_reset_releases_receipt ON fc_receipt_fbs_reset_releases(receipt_line_id)'))
    # r80 migration for an already installed r79: replay only explicit clean-reset
    # adjustment lines and release at most the exact quantity physically removed by
    # that reset. This deliberately does NOT make normal shipping/write-offs reloadable.
    if migrate_r79:_migrate_r79_clean_resets(db)


def params(c,w):return {'c':str(c),'w':str(w)}
def num(x):return Decimal(str(x or 0))


def _bucket_live_fbs(db,c,w,sku_id,location_id,container_id):
    """Current local FBS quantity in one physical bucket, derived from live balances."""
    row=db.execute(text('''SELECT COALESCE(b.quantity,0) physical,COALESCE(v.storage_only,0) storage_only
        FROM inventory_balances b
        LEFT JOIN fc_stocks_v2 v ON v.client_id=CAST(b.client_id AS text) AND v.warehouse_id=CAST(b.warehouse_id AS text)
          AND v.sku_id=CAST(b.sku_id AS text) AND v.location_id=CAST(b.location_id AS text)
          AND v.container_key=COALESCE(CAST(b.container_id AS text),'')
        WHERE b.client_id=:c AND b.warehouse_id=:w AND CAST(b.sku_id AS text)=:sku
          AND CAST(b.location_id AS text)=:loc
          AND COALESCE(CAST(b.container_id AS text),'')=:box
        LIMIT 1'''),{**params(c,w),'sku':str(sku_id),'loc':str(location_id or ''),'box':str(container_id or '')}).mappings().first()
    if not row:return Decimal(0)
    physical=max(Decimal(0),num(row['physical']))
    storage=min(physical,max(Decimal(0),num(row['storage_only'])))
    return max(Decimal(0),physical-storage)


def _reload_after_reset(db,c,w,sku_id,location_id,container_id,reset_at):
    """Quantity explicitly recreated by the r80 reload path after a historical reset."""
    box=str(container_id or '')
    return max(Decimal(0),num(db.execute(text('''SELECT COALESCE(SUM(l.quantity),0)
        FROM inventory_operation_lines l JOIN inventory_operations o ON o.id=l.operation_id
        WHERE o.client_id=:c AND o.warehouse_id=:w AND CAST(l.sku_id AS text)=:sku
          AND o.created_at>:after AND COALESCE(l.reason,'')='RECEIPT_FBS_RELOAD_AFTER_CLEAN_RESET'
          AND ((:box<>'' AND COALESCE(CAST(l.destination_container_id AS text),'')=:box)
            OR (:box='' AND l.destination_container_id IS NULL AND CAST(l.destination_location_id AS text)=:loc))'''),
        {**params(c,w),'sku':str(sku_id),'loc':str(location_id or ''),'box':box,'after':reset_at}).scalar()))


def _live_receipt_allocations(db,c,w,sku_ids):
    """Current factual FBS attribution for receipt lines, capped by live local FBS."""
    ids=sorted({str(x) for x in sku_ids if x})
    if not ids:return {}
    rows=db.execute(text('''SELECT t.line_id,t.quantity,t.updated_at,o.created_at receipt_created_at,
            CAST(l.sku_id AS text) sku_id,
            CAST(CASE WHEN l.destination_container_id IS NOT NULL THEN box.location_id ELSE l.destination_location_id END AS text) location_id,
            COALESCE(CAST(l.destination_container_id AS text),'') container_id,
            COALESCE(b.quantity,0) physical,COALESCE(v.storage_only,0) storage_only
        FROM fc_wh_receipt_fbs t
        JOIN inventory_operation_lines l ON CAST(l.id AS text)=t.line_id
        JOIN inventory_operations o ON o.id=l.operation_id
        LEFT JOIN containers box ON box.id=l.destination_container_id AND box.client_id=:c AND box.warehouse_id=:w
        LEFT JOIN inventory_balances b ON b.client_id=:c AND b.warehouse_id=:w AND b.sku_id=l.sku_id
          AND b.location_id=CASE WHEN l.destination_container_id IS NOT NULL THEN box.location_id ELSE l.destination_location_id END
          AND COALESCE(CAST(b.container_id AS text),'')=COALESCE(CAST(l.destination_container_id AS text),'')
        LEFT JOIN fc_stocks_v2 v ON v.client_id=CAST(b.client_id AS text) AND v.warehouse_id=CAST(b.warehouse_id AS text)
          AND v.sku_id=CAST(b.sku_id AS text) AND v.location_id=CAST(b.location_id AS text)
          AND v.container_key=COALESCE(CAST(b.container_id AS text),'')
        WHERE o.client_id=:c AND o.warehouse_id=:w AND CAST(l.sku_id AS text) IN :ids AND t.quantity>0
        ORDER BY t.updated_at DESC,o.created_at DESC,t.line_id DESC''').bindparams(bindparam('ids',expanding=True)),
        {**params(c,w),'ids':ids}).mappings().all()
    remaining={};alloc={}
    for raw in rows:
        r=dict(raw);key=(r['sku_id'],r['location_id'] or '',r['container_id'] or '')
        if key not in remaining:
            physical=max(Decimal(0),num(r['physical']))
            storage=min(physical,max(Decimal(0),num(r['storage_only'])))
            remaining[key]=max(Decimal(0),physical-storage)
        marker=max(Decimal(0),num(r['quantity']))
        take=min(marker,remaining[key])
        alloc[str(r['line_id'])]=take
        remaining[key]-=take
    return alloc




def _live_receipt_allocations_for_lines(db,c,w,line_ids):
    """Fast factual FBS attribution for a limited receipt-history page.

    The old history list expanded every historical receipt marker for every SKU that
    appeared on the page. A popular SKU spread across many boxes/receipts could make
    the page time out. This query limits the expensive allocation window to the exact
    physical buckets referenced by the page, while still ordering *all* markers in
    those buckets so the factual attribution stays identical to detail().
    """
    ids=sorted({str(x) for x in line_ids if x})
    if not ids:return {}
    # r96: client_id/warehouse_id are UUID in the physical tables but text in
    # fc_stocks_v2. Reusing one bind parameter against both types lets PostgreSQL
    # infer it as UUID and then fail on the text side (operator does not exist:
    # text = uuid). Compare every scope key as text in this CTE so :c/:w have one
    # unambiguous type across the whole statement.
    sql=text('''WITH targets AS (
        SELECT CAST(l.id AS text) line_id,CAST(l.sku_id AS text) sku_id,
          CAST(CASE WHEN l.destination_container_id IS NOT NULL THEN bx.location_id ELSE l.destination_location_id END AS text) location_id,
          COALESCE(CAST(l.destination_container_id AS text),'') container_id
        FROM inventory_operation_lines l
        LEFT JOIN containers bx ON bx.id=l.destination_container_id
          AND CAST(bx.client_id AS text)=:c AND CAST(bx.warehouse_id AS text)=:w
        WHERE CAST(l.id AS text) IN :line_ids
      ), target_buckets AS (
        SELECT DISTINCT sku_id,location_id,container_id FROM targets
      ), markers AS (
        SELECT t.line_id,t.quantity,t.updated_at,o.created_at receipt_created_at,
          CAST(l.sku_id AS text) sku_id,
          CAST(CASE WHEN l.destination_container_id IS NOT NULL THEN bx.location_id ELSE l.destination_location_id END AS text) location_id,
          COALESCE(CAST(l.destination_container_id AS text),'') container_id
        FROM fc_wh_receipt_fbs t
        JOIN inventory_operation_lines l ON CAST(l.id AS text)=t.line_id
        JOIN inventory_operations o ON o.id=l.operation_id
          AND CAST(o.client_id AS text)=:c AND CAST(o.warehouse_id AS text)=:w
        LEFT JOIN containers bx ON bx.id=l.destination_container_id
          AND CAST(bx.client_id AS text)=:c AND CAST(bx.warehouse_id AS text)=:w
        JOIN target_buckets tb ON tb.sku_id=CAST(l.sku_id AS text)
          AND tb.location_id=CAST(CASE WHEN l.destination_container_id IS NOT NULL THEN bx.location_id ELSE l.destination_location_id END AS text)
          AND tb.container_id=COALESCE(CAST(l.destination_container_id AS text),'')
        WHERE t.quantity>0
      ), ranked AS (
        SELECT m.*,
          COALESCE(SUM(m.quantity) OVER (
            PARTITION BY m.sku_id,m.location_id,m.container_id
            ORDER BY m.updated_at DESC,m.receipt_created_at DESC,m.line_id DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),0) previous_quantity
        FROM markers m
      ), live AS (
        SELECT tb.sku_id,tb.location_id,tb.container_id,
          GREATEST(0,COALESCE(b.quantity,0)-LEAST(COALESCE(b.quantity,0),GREATEST(0,COALESCE(v.storage_only,0)))) live_fbs
        FROM target_buckets tb
        LEFT JOIN inventory_balances b ON CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w
          AND CAST(b.sku_id AS text)=tb.sku_id AND CAST(b.location_id AS text)=tb.location_id
          AND COALESCE(CAST(b.container_id AS text),'')=tb.container_id
        LEFT JOIN fc_stocks_v2 v ON CAST(v.client_id AS text)=:c AND CAST(v.warehouse_id AS text)=:w
          AND v.sku_id=tb.sku_id AND v.location_id=tb.location_id AND v.container_key=tb.container_id
      )
      SELECT r.line_id,
        LEAST(r.quantity,GREATEST(0,l.live_fbs-r.previous_quantity)) allocated
      FROM ranked r JOIN live l USING(sku_id,location_id,container_id)
      JOIN targets t ON t.line_id=r.line_id''').bindparams(bindparam('line_ids',expanding=True))
    rows=db.execute(sql,{**params(c,w),'line_ids':ids}).mappings().all()
    return {str(r['line_id']):max(Decimal(0),num(r['allocated'])) for r in rows}

def _receipt_candidates_for_bucket(db,c,w,sku_id,location_id,container_id,before=None):
    'Newest receipt allocations for one physical bucket, locked for exact release.'
    box=str(container_id or '')
    where_before=' AND t.updated_at<:before' if before is not None else ''
    sql='''SELECT t.line_id,t.quantity,t.reloadable_quantity,t.updated_at,o.created_at receipt_created_at
        FROM fc_wh_receipt_fbs t
        JOIN inventory_operation_lines l ON CAST(l.id AS text)=t.line_id
        JOIN inventory_operations o ON o.id=l.operation_id
        WHERE o.client_id=:c AND o.warehouse_id=:w AND CAST(l.sku_id AS text)=:sku
          AND t.quantity>0
          AND ((:box<>'' AND COALESCE(CAST(l.destination_container_id AS text),'')=:box)
            OR (:box='' AND l.destination_container_id IS NULL AND CAST(l.destination_location_id AS text)=:loc))'''+where_before+'''
        ORDER BY t.updated_at DESC,o.created_at DESC,t.line_id DESC
        FOR UPDATE OF t'''
    values={**params(c,w),'sku':str(sku_id),'loc':str(location_id or ''),'box':box}
    if before is not None:values['before']=before
    return [dict(r) for r in db.execute(text(sql),values).mappings().all()]


def _release_bucket_quantity(db,c,w,sku_id,location_id,container_id,quantity,*,reloadable,reset_line_id=None,before=None):
    'Release at most quantity from receipt markers for one stock bucket.'
    remaining=max(Decimal(0),num(quantity));released=Decimal(0);rows=0
    if remaining<=0:return {'rows':0,'quantity':Decimal(0)}
    if reset_line_id:
        already=num(db.execute(text('''SELECT COALESCE(SUM(quantity),0)
            FROM fc_receipt_fbs_reset_releases WHERE reset_line_id=:id'''),{'id':str(reset_line_id)}).scalar())
        remaining=max(Decimal(0),remaining-already)
        if remaining<=0:return {'rows':0,'quantity':Decimal(0)}
    candidates=_receipt_candidates_for_bucket(db,c,w,sku_id,location_id,container_id,before=before)
    for row in candidates:
        if remaining<=0:break
        take=min(remaining,max(Decimal(0),num(row['quantity'])))
        if take<=0:continue
        if reset_line_id:
            # The reset-line ledger makes migration/release idempotent. Insert first;
            # only this exact release record may reduce the receipt marker.
            inserted=db.execute(text('''INSERT INTO fc_receipt_fbs_reset_releases(
                    reset_line_id,receipt_line_id,quantity) VALUES(:reset,:receipt,:q)
                ON CONFLICT(reset_line_id,receipt_line_id) DO NOTHING
                RETURNING quantity'''),{'reset':str(reset_line_id),'receipt':row['line_id'],'q':take}).scalar_one_or_none()
            if inserted is None:continue
        if reloadable:
            db.execute(text('''UPDATE fc_wh_receipt_fbs
                SET quantity=GREATEST(0,quantity-:q),
                    reloadable_quantity=COALESCE(reloadable_quantity,0)+:q,
                    updated_at=CURRENT_TIMESTAMP WHERE line_id=:id'''),{'q':take,'id':row['line_id']})
        else:
            db.execute(text('''UPDATE fc_wh_receipt_fbs
                SET quantity=GREATEST(0,quantity-:q),updated_at=CURRENT_TIMESTAMP
                WHERE line_id=:id'''),{'q':take,'id':row['line_id']})
        remaining-=take;released+=take;rows+=1
    return {'rows':rows,'quantity':released}


def _release_from_reset_operation(db,operation_id):
    'Convert one FBS-CLEAN-RESET operation into exact receipt reload credits.'
    reset_rows=[dict(r) for r in db.execute(text('''SELECT CAST(l.id AS text) reset_line_id,
            CAST(o.client_id AS text) client_id,CAST(o.warehouse_id AS text) warehouse_id,
            CAST(l.sku_id AS text) sku_id,l.quantity,
            CAST(l.source_location_id AS text) location_id,CAST(l.source_container_id AS text) container_id,
            o.created_at reset_at
        FROM inventory_operation_lines l JOIN inventory_operations o ON o.id=l.operation_id
        WHERE CAST(o.id AS text)=:id AND o.operation_type='ADJUSTMENT'
          AND COALESCE(o.reference,'') LIKE 'FBS-CLEAN-RESET-%'
        ORDER BY l.id'''),{'id':str(operation_id)}).mappings().all()]
    total=Decimal(0);rows=0
    for r in reset_rows:
        qty=abs(num(r['quantity']))
        if qty<=0 or not r.get('location_id'):continue
        result=_release_bucket_quantity(
            db,r['client_id'],r['warehouse_id'],r['sku_id'],r['location_id'],r.get('container_id'),qty,
            reloadable=True,reset_line_id=r['reset_line_id'],before=r['reset_at'],
        )
        released=num(result['quantity']);row_count=int(result['rows'])
        # r82 historical recovery: if the bucket is factually FBS=0, an old marker
        # touched after the reset must not keep pretending that stock is still in FBS.
        # Never use this fallback when a controlled reload happened after that reset.
        if released<qty and _bucket_live_fbs(db,r['client_id'],r['warehouse_id'],r['sku_id'],r['location_id'],r.get('container_id'))<=0 \
                and _reload_after_reset(db,r['client_id'],r['warehouse_id'],r['sku_id'],r['location_id'],r.get('container_id'),r['reset_at'])<=0:
            fallback=_release_bucket_quantity(
                db,r['client_id'],r['warehouse_id'],r['sku_id'],r['location_id'],r.get('container_id'),qty,
                reloadable=True,reset_line_id=r['reset_line_id'],before=None,
            )
            released+=num(fallback['quantity']);row_count+=int(fallback['rows'])
        total+=released;rows+=row_count
    return {'rows':rows,'quantity':float(total),'reloadable':True}


def _migrate_r79_clean_resets(db):
    # Only reset operations with an amount not yet represented in the release ledger
    # need work. The exact per-line ledger makes this safe if install_schema is retried.
    op_ids=db.execute(text('''SELECT DISTINCT CAST(o.id AS text)
        FROM inventory_operations o JOIN inventory_operation_lines l ON l.operation_id=o.id
        LEFT JOIN (
            SELECT reset_line_id,SUM(quantity) released
            FROM fc_receipt_fbs_reset_releases GROUP BY reset_line_id
        ) x ON x.reset_line_id=CAST(l.id AS text)
        WHERE o.operation_type='ADJUSTMENT' AND COALESCE(o.reference,'') LIKE 'FBS-CLEAN-RESET-%'
          AND ABS(COALESCE(l.quantity,0))>COALESCE(x.released,0)
        ORDER BY CAST(o.id AS text)''')).scalars().all()
    for operation_id in op_ids:_release_from_reset_operation(db,operation_id)


def release_for_fbs_zero(db,c,w,sku_ids,reloadable=True,reset_operation_id=None,released_buckets=None):
    'Release receipt->FBS markers after an intentional FBS-to-zero operation.'
    ids=[str(x) for x in sku_ids if x]
    if not ids:return {'rows':0,'quantity':0.0,'reloadable':bool(reloadable)}
    if reloadable and reset_operation_id:
        return _release_from_reset_operation(db,reset_operation_id)
    total=Decimal(0);row_count=0
    for bucket in released_buckets or []:
        if str(bucket.get('sku_id') or '') not in ids:continue
        result=_release_bucket_quantity(
            db,c,w,bucket.get('sku_id'),bucket.get('location_id'),bucket.get('container_id'),bucket.get('quantity'),
            reloadable=bool(reloadable),
        )
        total+=num(result['quantity']);row_count+=int(result['rows'])
    return {'rows':row_count,'quantity':float(total),'reloadable':bool(reloadable)}

def get_operation(db,c,w,oid):
    op=db.execute(select(InventoryOperation).where(InventoryOperation.id==uuid.UUID(str(oid)),
        InventoryOperation.client_id==uuid.UUID(str(c)),InventoryOperation.warehouse_id==uuid.UUID(str(w)),
        InventoryOperation.operation_type.in_(['RECEIVING','RECEIVING_IMPORT'])).execution_options(populate_existing=True)).scalar_one_or_none()
    if not op:raise HTTPException(404,detail='Приёмка не найдена у выбранного клиента на этом складе')
    return op


def _tsd_work_fact(db,op):
    idem=str(op.idempotency_key or '')
    if not idem.startswith('tsd-work-'):return {}
    wid=idem[len('tsd-work-'):]
    row=db.execute(text("SELECT data,created_at,updated_at FROM fc_tsd_work WHERE id=:id LIMIT 1"),{'id':wid}).mappings().first()
    if not row:return {}
    data=row['data'] if isinstance(row['data'],dict) else json.loads(row['data'] or '{}')
    return {'work_id':wid,'first_item_at':data.get('first_item_at'),'last_item_at':data.get('last_item_at'),
        'server_posted_at':op.created_at,'work_updated_at':row['updated_at'],'lines':data.get('lines') or {},
        'receipt_number':str(data.get('receipt_number') or '')}

def metadata(db,op):
    structured=db.execute(text('''SELECT r.id,r.name,r.status,p.stock_class FROM fc_structured_receipts r
        LEFT JOIN fc_structured_receipt_policy p ON p.receipt_id=r.id
        WHERE r.client_id=:c AND r.warehouse_id=:w AND r.committed_operation_id=:id'''),
        {**params(op.client_id,op.warehouse_id),'id':str(op.id)}).mappings().first()
    comment=str(op.comment or '').upper();idem=str(op.idempotency_key or '');kind='EXCEL' if op.operation_type=='RECEIVING_IMPORT' else 'MANUAL'
    if idem.startswith('tsd-work-'):kind='TSD'
    if idem.upper().startswith('PVZ:') or 'ПРИЁМКА ПВЗ' in comment:kind='PVZ'
    if structured:kind='STRUCTURED'
    zone=(structured['stock_class'] or 'FBS') if structured else ('MAIN' if kind in ('TSD','PVZ') or any(x in comment for x in ('ЗОНА:MAIN','ОСНОВНОЙ СКЛАД','STORAGE_ONLY','ВНУТРЕННИЙ УЧЁТ')) else 'FBS' if 'ЗОНА:FBS' in comment else 'LEGACY')
    status='CANCELLED' if op.status=='CANCELLED' or structured and structured['status']=='CANCELLED' else str(op.status or 'COMPLETED')
    reference=(structured['name'] if structured else None) or op.reference or 'Приёмка'
    if kind=='PVZ' and re.fullmatch(r'ПВЗ\s+[0-9a-fA-F-]{32,36}',str(reference or '').strip()):reference='Приёмка ПВЗ'
    fact=_tsd_work_fact(db,op) if kind=='TSD' else {}
    return {'id':str(op.id),'type':kind,'zone':zone,'status':status,'created_at':fact.get('last_item_at') or op.created_at,
        'posted_at':op.created_at,'factual_started_at':fact.get('first_item_at'),'factual_received_at':fact.get('last_item_at'),
        'performed_by':op.performed_by,'reference':reference,
        'comment':op.comment,'structured_receipt_id':structured['id'] if structured else None}


def recent(db,c,w,kind='',limit=100,offset=0,query=''):
    # Operation type, not a translated comment prefix, defines a posted receipt.
    p={**params(c,w),'limit':limit,'offset':offset,'q':'%'+query.strip()+'%','kind':str(kind or '').upper()}
    sql='''FROM inventory_operations o
        LEFT JOIN fc_structured_receipts r ON r.committed_operation_id=CAST(o.id AS text)
          AND r.client_id=CAST(o.client_id AS text) AND r.warehouse_id=CAST(o.warehouse_id AS text)
        LEFT JOIN fc_structured_receipt_policy policy ON policy.receipt_id=r.id
        LEFT JOIN fc_tsd_work tw ON o.idempotency_key=('tsd-work-' || tw.id)
          AND tw.client_id=CAST(o.client_id AS text) AND tw.warehouse_id=CAST(o.warehouse_id AS text)
        WHERE o.client_id=:c AND o.warehouse_id=:w AND o.operation_type IN ('RECEIVING','RECEIVING_IMPORT')
          AND (:kind='' OR (:kind='MANUAL' AND o.operation_type='RECEIVING' AND o.idempotency_key NOT LIKE 'tsd-work-%'
                AND o.idempotency_key NOT ILIKE 'PVZ:%' AND COALESCE(o.comment,'') NOT ILIKE '%ПРИЁМКА ПВЗ%')
            OR (:kind IN ('EXCEL','BULK') AND o.operation_type='RECEIVING_IMPORT')
            OR (:kind='TSD' AND o.idempotency_key LIKE 'tsd-work-%')
            OR (:kind='PVZ' AND o.operation_type='RECEIVING' AND (o.idempotency_key ILIKE 'PVZ:%' OR COALESCE(o.comment,'') ILIKE '%ПРИЁМКА ПВЗ%')))
          AND (:q='%%' OR COALESCE(r.name,'') ILIKE :q OR COALESCE(o.reference,'') ILIKE :q
            OR COALESCE(o.comment,'') ILIKE :q OR COALESCE(o.performed_by,'') ILIKE :q OR CAST(o.id AS text) ILIKE :q)'''
    total=int(db.execute(text('SELECT COUNT(*) '+sql),p).scalar())
    records=db.execute(text('''SELECT CAST(o.id AS text) id,o.operation_type,o.idempotency_key,o.created_at,o.performed_by,o.reference,o.comment,
        CASE WHEN r.status='CANCELLED' THEN 'CANCELLED' ELSE COALESCE(o.status,'COMPLETED') END status,
        r.id structured_receipt_id,r.name receipt_name,policy.stock_class,tw.data->>'first_item_at' factual_started_at,tw.data->>'last_item_at' factual_received_at '''+sql+' ORDER BY o.created_at DESC,o.id DESC LIMIT :limit OFFSET :offset'),p).mappings().all()
    result=[]
    if not records:return {'rows':[],'total':total,'offset':offset,'limit':limit}
    totals={r['id']:r for r in db.execute(text('''SELECT CAST(l.operation_id AS text) id,COUNT(*) lines,
        SUM(l.quantity) units,COALESCE(SUM(t.quantity),0) transferred,
        COALESCE(SUM(t.reloadable_quantity),0) reloadable
        FROM inventory_operation_lines l LEFT JOIN fc_wh_receipt_fbs t ON t.line_id=CAST(l.id AS text)
        WHERE l.operation_id IN :ids GROUP BY l.operation_id''').bindparams(bindparam('ids',expanding=True)),
        {'ids':[uuid.UUID(r['id']) for r in records]}).mappings().all()}
    receipt_lines=[dict(x) for x in db.execute(text('''SELECT CAST(l.id AS text) line_id,CAST(l.operation_id AS text) operation_id,CAST(l.sku_id AS text) sku_id
        FROM inventory_operation_lines l WHERE l.operation_id IN :ids''').bindparams(bindparam('ids',expanding=True)),
        {'ids':[uuid.UUID(r['id']) for r in records]}).mappings().all()]
    # r86: calculate factual FBS only for the receipt lines on this page. The SQL
    # still considers every historical marker in those exact buckets, but avoids the
    # warehouse-wide SKU expansion that caused receipt-history Request timeout.
    live_alloc=_live_receipt_allocations_for_lines(db,c,w,[x['line_id'] for x in receipt_lines])
    live_by_operation={}
    for x in receipt_lines:
        live_by_operation[x['operation_id']]=live_by_operation.get(x['operation_id'],Decimal(0))+num(live_alloc.get(x['line_id'],0))
    for r in records:
        r=dict(r);s=totals.get(r['id'],{});comment=str(r['comment'] or '').upper();idem=str(r['idempotency_key'] or '')
        is_pvz=idem.upper().startswith('PVZ:') or 'ПРИЁМКА ПВЗ' in comment
        r['type']='STRUCTURED' if r['structured_receipt_id'] else 'TSD' if idem.startswith('tsd-work-') else 'PVZ' if is_pvz else 'EXCEL' if r['operation_type']=='RECEIVING_IMPORT' else 'MANUAL'
        r['zone']=(r['stock_class'] or 'FBS') if r['structured_receipt_id'] else 'MAIN' if r['type'] in ('TSD','PVZ') or any(x in comment for x in ('ЗОНА:MAIN','ОСНОВНОЙ СКЛАД','STORAGE_ONLY','ВНУТРЕННИЙ УЧЁТ')) else 'FBS' if 'ЗОНА:FBS' in comment else 'LEGACY'
        reference=r['receipt_name'] or r['reference'] or 'Приёмка'
        if r['type']=='PVZ' and re.fullmatch(r'ПВЗ\s+[0-9a-fA-F-]{32,36}',str(reference or '').strip()):reference='Приёмка ПВЗ'
        posted_at=r.get('created_at')
        if r['type']=='TSD' and r.get('factual_received_at'):r['created_at']=r['factual_received_at']
        r.update(reference=reference,posted_at=posted_at,lines=int(s.get('lines',0)),units=float(s.get('units',0)),
            transferred=float(live_by_operation.get(r['id'],0)),tracked_transferred=float(s.get('transferred',0)),reloadable=float(s.get('reloadable',0)))
        result.append(r)
    return {'rows':result,'total':total,'offset':offset,'limit':limit}


def detail(db,c,w,oid,include_marketplace=True):
    op=get_operation(db,c,w,oid);meta=metadata(db,op)
    # A whole-box move retains container identity. Loose stock stays bound to its receipt address.
    rows=db.execute(text('''SELECT CAST(l.id AS text) line_id,CAST(l.sku_id AS text) sku_id,l.quantity quantity_received,
        CAST(l.destination_location_id AS text) original_location_id,
        CAST(CASE WHEN l.destination_container_id IS NOT NULL THEN box.location_id ELSE l.destination_location_id END AS text) location_id,
        CAST(l.destination_container_id AS text) container_id,box.status box_status,
        CASE WHEN box.id IS NULL THEN orig.code ELSE loc.code END location,
        COALESCE(box.name,box.barcode,'') container,s.barcode,s.article,s.size,s.name,
        COALESCE(t.quantity,0) transferred,COALESCE(t.reloadable_quantity,0) reloadable,
        COALESCE(b.quantity,0) physical,COALESCE(b.version,0) physical_version,
        COALESCE(v.storage_only,0) storage_raw,COALESCE(v.fbs_available,0) fbs_available,
        COALESCE(v.reserved,0) reserved,COALESCE(v.version,0) mirror_version,
        CASE WHEN box.id IS NULL THEN orig.is_active ELSE loc.is_active END location_active
        FROM inventory_operation_lines l JOIN skus s ON s.id=l.sku_id AND s.client_id=:c
        LEFT JOIN containers box ON box.id=l.destination_container_id AND box.client_id=:c AND box.warehouse_id=:w
        LEFT JOIN locations orig ON orig.id=l.destination_location_id AND orig.warehouse_id=:w
        LEFT JOIN locations loc ON loc.id=box.location_id AND loc.warehouse_id=:w
        LEFT JOIN fc_wh_receipt_fbs t ON t.line_id=CAST(l.id AS text)
        LEFT JOIN inventory_balances b ON b.client_id=:c AND b.warehouse_id=:w AND b.sku_id=l.sku_id
          AND b.location_id=CASE WHEN l.destination_container_id IS NOT NULL THEN box.location_id ELSE l.destination_location_id END
          AND COALESCE(b.container_id::text,'')=COALESCE(l.destination_container_id::text,'')
        LEFT JOIN fc_stocks_v2 v ON v.client_id=CAST(b.client_id AS text) AND v.warehouse_id=CAST(b.warehouse_id AS text)
          AND v.sku_id=CAST(b.sku_id AS text) AND v.location_id=CAST(b.location_id AS text) AND v.container_key=COALESCE(b.container_id::text,'')
        WHERE l.operation_id=:oid ORDER BY l.id'''),{**params(c,w),'oid':uuid.UUID(str(oid))}).mappings().all()
    holds={(r['sku_id'],r['location_id'],r['box']):num(r['quantity']) for r in db.execute(text('''
        SELECT l.sku_id,l.location_id,COALESCE(l.container_id,'') box,
        SUM(GREATEST(l.allocated_qty,l.scan_qty+l.manual_qty+l.missing_qty)) quantity
        FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
        WHERE t.client_id=:c AND t.warehouse_id=:w AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')
        GROUP BY l.sku_id,l.location_id,COALESCE(l.container_id,'')'''),params(c,w)).mappings().all()}
    # r92: opening/exporting one receipt must not expand every historical marker for
    # each SKU in the warehouse. Limit factual FBS attribution to the exact receipt
    # lines/buckets; the helper still ranks all markers in those buckets.
    live_alloc=_live_receipt_allocations_for_lines(db,c,w,[r['line_id'] for r in rows])
    tsd_fact=_tsd_work_fact(db,op) if meta.get('type')=='TSD' else {}
    tsd_lines=tsd_fact.get('lines') or {}
    remaining={};out=[];stamp=[]
    for raw in rows:
        r=dict(raw);key=(r['sku_id'],r['location_id'],r['container_id'] or '')
        physical=max(Decimal(0),num(r['physical']));main=max(Decimal(0),min(num(r['storage_raw']),physical-num(r['fbs_available'])-num(r['reserved'])))
        free=max(Decimal(0),main-holds.get(key,Decimal(0)))
        remaining.setdefault(key,free)
        tracked=max(Decimal(0),num(r['transferred']));current=max(Decimal(0),num(live_alloc.get(r['line_id'],0)));reloadable=max(Decimal(0),num(r['reloadable']))
        cap=max(Decimal(0),num(r['quantity_received'])-current)
        valid=meta['status']=='COMPLETED' and meta['zone']!='FBS' and r['location_active'] and (not r['container_id'] or r['box_status']=='ACTIVE')
        main_available=min(cap,remaining[key]) if valid else Decimal(0)
        remaining[key]-=main_available
        reload_available=min(reloadable,max(Decimal(0),cap-main_available)) if valid else Decimal(0)
        available=main_available+reload_available
        factual=tsd_lines.get(str(r['sku_id'])) or {}
        r.update(first_scanned_at=factual.get('first_scanned_at'),last_scanned_at=factual.get('last_scanned_at'),
            main_now=float(main),fbs_now=float(max(Decimal(0),physical-main)),available_quantity=int(available),
            main_available_quantity=int(main_available),reloadable_quantity=int(reloadable),reloadable_available_quantity=int(reload_available),
            held=float(holds.get(key,0)),quantity_received=float(r['quantity_received']),transferred=float(current),tracked_transferred=float(tracked),physical=float(physical),
            initially_fbs=meta['zone']=='FBS')
        stamp.append([r[k] for k in ('line_id','location_id','container_id','physical_version','mirror_version','transferred','reloadable_quantity','available_quantity')])
        out.append(r)
    events=[dict(r) for r in db.execute(text('''SELECT id,actor,created_at,result_json FROM fc_receipt_fbs_events
        WHERE client_id=:c AND warehouse_id=:w AND operation_id=:id ORDER BY created_at DESC'''),{**params(c,w),'id':str(oid)}).mappings().all()]
    publications=[];wb_state={};paused=False
    if include_marketplace:
        publications=[dict(r) for r in db.execute(text('''SELECT q.product_key barcode,q.status,q.desired_amount,q.last_error,q.updated_at
            FROM fc_marketplace_stock_outbox_v2 q WHERE q.client_id=:c AND q.warehouse_id=:w AND q.provider='WB'
              AND q.sku_id IN (SELECT CAST(sku_id AS text) FROM inventory_operation_lines WHERE operation_id=:oid)'''),
            {**params(c,w),'oid':uuid.UUID(str(oid))}).mappings().all()]
        from app.services.wb_sales_control import global_paused
        from app.services.wb_sale_status import snapshot
        wb_state=snapshot(db,c,w,{r['sku_id'] for r in out});paused=global_paused(db,c,w)
    return {'receipt':meta,'lines':out,'fingerprint':stock.fingerprint([meta['status'],meta['zone'],stamp]),
        'available_total':sum(r['available_quantity'] for r in out),'transferred_total':sum(r['transferred'] for r in out),
        'reloadable_total':sum(r['reloadable_quantity'] for r in out),'events':events,'publications':publications,
        'wb_state':wb_state,'global_paused':paused}


def queue_publication(db,c,w,sku_ids,requested_wb_id=None):
    from app.routers.receiving import _active_wb_credential,_work_wb_warehouse_id
    from app.services.stock_core_v2 import enqueue_wb_stock_for_sku,wb_sales_paused
    from app.services.product_identity import wb_product_identity_map
    cred=_active_wb_credential(db,uuid.UUID(str(c)))
    # Resolve within this physical warehouse; a filter left on the WB screen is
    # not permission to publish another warehouse's stock.
    wb_id=_work_wb_warehouse_id(cred,None,w) if cred else None
    mapping_error=''
    from app.services.wb_sale_status import resolve
    try:cred,wb_id=resolve(db,c,w)
    except HTTPException as e:mapping_error=e.detail
    if requested_wb_id and cred:
        scopes=(cred.warehouse_settings or {}).get('__fc_physical_warehouses__',{})
        config=(scopes.get(str(w)) or {}).get(str(requested_wb_id)) or {}
        mode=config.get('mode') if isinstance(config,dict) else config
        if str(mode).upper()!='WORK':mapping_error='Склад WB не назначен рабочим для выбранного физического склада'
        else:wb_id=int(requested_wb_id)
    skus=db.execute(select(SKU).where(SKU.client_id==uuid.UUID(str(c)),SKU.id.in_([uuid.UUID(str(x)) for x in sku_ids]))).scalars().all()
    cards=wb_product_identity_map(db,uuid.UUID(str(c)),{s.barcode for s in skus if s.barcode})
    output=[]
    for sku in skus:
        error='Подключение WB не настроено' if not cred else mapping_error or ('Не выбран рабочий склад WB' if not wb_id else 'Нет карточки WB для этого ШК' if not cards.get(sku.barcode) else '')
        if error:output.append({'barcode':sku.barcode,'status':'NOT_QUEUED','error':error});continue
        desired=enqueue_wb_stock_for_sku(db,client_id=c,warehouse_id=w,sku=sku,credential_id=cred.id,external_warehouse_id=wb_id,reason='RECEIPT_TO_FBS')
        output.append({'barcode':sku.barcode,'status':'PAUSED' if wb_sales_paused(db,client_id=c,warehouse_id=w,sku_id=sku.id) else 'PENDING','desired_amount':desired})
    return output


def transfer(db,c,w,oid,request,actor):
    # Shared warehouse/sale locks prevent picking and classification racing each other.
    op=get_operation(db,c,w,oid)
    ids=db.execute(text('SELECT DISTINCT CAST(sku_id AS text) FROM inventory_operation_lines WHERE operation_id=:id'),{'id':op.id}).scalars().all()
    stock.locks(db,str(c),str(w),ids)
    # Refresh after taking the shared lock: cancellation may have completed while we waited.
    op=get_operation(db,c,w,oid)
    if metadata(db,op)['status']!='COMPLETED':raise HTTPException(409,detail='Приёмка отменена или не проведена')
    digest=stock.fingerprint({'operation_id':str(oid),'request':request})
    eid=str(request['request_id'])
    seen=db.execute(text('SELECT * FROM fc_receipt_fbs_events WHERE id=:id FOR UPDATE'),{'id':eid}).mappings().first()
    if seen:
        if seen['client_id']!=str(c) or seen['warehouse_id']!=str(w) or seen['request_hash']!=digest:raise HTTPException(409,detail='Этот запрос уже использован с другим составом')
        return {**seen['result_json'],'replayed':True}
    data=detail(db,c,w,oid)
    if data['receipt']['status']!='COMPLETED':raise HTTPException(409,detail='Приёмка отменена или не проведена')
    if request.get('fingerprint')!=data['fingerprint']:raise HTTPException(409,detail='Остаток или состав приёмки изменился. Обновите состав перед переводом')
    by_id={r['line_id']:r for r in data['lines']};chosen=request['lines']
    if not chosen or len({r['line_id'] for r in chosen})!=len(chosen):raise HTTPException(422,detail='Выберите строки без повторений')
    updates=[];affected=set()
    for x in chosen:
        row=by_id.get(x['line_id']);q=num(x['quantity'])
        if not row:raise HTTPException(422,detail='Выбранная строка не относится к этой приёмке')
        if not q.is_finite() or q<=0 or q!=q.to_integral_value():raise HTTPException(422,detail='Количество должно быть положительным целым числом')
        if q>num(row['available_quantity']):raise HTTPException(409,detail=f"{row['barcode']} · {row['container'] or row['location']}: доступно {row['available_quantity']} шт.")
        from_main=min(q,num(row.get('main_available_quantity')))
        restored=q-from_main
        if restored>num(row.get('reloadable_available_quantity')):raise HTTPException(409,detail='Доступное количество изменилось. Обновите состав приёмки')
        updates.append((row,q,from_main,restored));affected.add(row['sku_id'])
    # Validate and classify the part that still physically exists in MAIN.
    grouped={}
    for row,_q,from_main,_restored in updates:
        if from_main<=0:continue
        key=(row['sku_id'],row['location_id'],row['container_id']);grouped[key]=grouped.get(key,Decimal(0))+from_main
    for (s,loc,box),q in sorted(grouped.items(),key=lambda x:str(x[0])):
        stock.place(db,c,w,loc,box);b=stock.bucket(db,c,w,s,loc,box)
        if b['main']!=b['storage_raw'] or q>max(Decimal(0),b['main']-b['main_held']):raise HTTPException(409,detail='Доступное количество изменилось. Обновите состав приёмки')
        stock.save_mirror(db,c,w,b,b['physical'],b['main']-q)
    # A clean FBS reset intentionally removed physical stock. Receipt reload credit is
    # the only path allowed to recreate that exact removed quantity, in the original/current
    # receipt bucket, without pretending that it is a second supplier receiving.
    restore_updates=[x for x in updates if x[3]>0]
    if restore_updates:
        from app.schemas.inventory import OperationCreate, StockLine
        from app.services.inventory import execute_operation
        from app.services.stock_core_v2 import reconcile_stock_bucket
        restore_lines=[StockLine(
            sku_id=uuid.UUID(str(row['sku_id'])),quantity=restored,
            destination_location_id=uuid.UUID(str(row['location_id'])),
            destination_container_id=uuid.UUID(str(row['container_id'])) if row.get('container_id') else None,
            reason='RECEIPT_FBS_RELOAD_AFTER_CLEAN_RESET',
        ) for row,_q,_from_main,restored in restore_updates]
        execute_operation(db,OperationCreate(
            client_id=uuid.UUID(str(c)),warehouse_id=uuid.UUID(str(w)),operation_type='ADJUSTMENT',
            idempotency_key=f'receipt-fbs-reload-{eid}',performed_by=actor,
            reference=f'RECEIPT-FBS-RELOAD-{oid}',
            comment='Повторная загрузка FBS из ранее обнулённой приёмки. Восстановлено только количество, удалённое чистым сбросом FBS.',
            lines=restore_lines,
        ),commit=False)
        for row,_q,_from_main,_restored in restore_updates:
            reconcile_stock_bucket(db,client_id=c,warehouse_id=w,sku_id=row['sku_id'],location_id=row['location_id'],
                container_id=row['container_id'],preferred_bucket='FBS_AVAILABLE',lock=True)
    for row,q,_from_main,restored in updates:
        current=num(row.get('transferred'));credit=max(Decimal(0),num(row.get('reloadable_quantity'))-restored)
        db.execute(text('''INSERT INTO fc_wh_receipt_fbs(line_id,quantity,reloadable_quantity,actor)
            VALUES(:id,:q,:credit,:actor)
            ON CONFLICT(line_id) DO UPDATE SET quantity=EXCLUDED.quantity,
                reloadable_quantity=EXCLUDED.reloadable_quantity,actor=EXCLUDED.actor,updated_at=now()'''),
            {'id':row['line_id'],'q':current+q,'credit':credit,'actor':actor})
    publication=queue_publication(db,c,w,affected,request.get('wb_warehouse_id'))
    result={'ok':True,'event_id':eid,'moved':sum(int(q) for _,q,_,_ in updates),
        'from_main':sum(int(x[2]) for x in updates),'restored_after_reset':sum(int(x[3]) for x in updates),
        'details':[{'line_id':r['line_id'],'sku_id':r['sku_id'],'barcode':r['barcode'],'location':r['location'],'container':r['container'],
                    'quantity':int(q),'from_main':int(from_main),'restored_after_reset':int(restored)} for r,q,from_main,restored in updates],
        'publication':publication,'replayed':False}
    db.execute(text('''INSERT INTO fc_receipt_fbs_events(id,client_id,warehouse_id,operation_id,request_hash,result_json,actor)
        VALUES(:id,:c,:w,:op,:hash,CAST(:result AS jsonb),:actor)'''),
        {**params(c,w),'id':eid,'op':str(oid),'hash':digest,'result':json.dumps(result,ensure_ascii=False),'actor':actor})
    return result
