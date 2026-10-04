"""Scoped, previewed inventory edits. Physical quantities always use the stock ledger."""
import hashlib
import json
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text

from app.services.warehouse_availability import held_quantity, lock_scope


def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_stock_action_plans (
      id varchar(36) PRIMARY KEY, client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL,
      request_json jsonb NOT NULL, preview_json jsonb NOT NULL, fingerprint varchar(64) NOT NULL,
      status varchar(16) NOT NULL DEFAULT 'PREVIEW', actor text, operation_id varchar(36),
      created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz)'''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_stock_action_scope ON fc_stock_action_plans(client_id,warehouse_id,created_at DESC)'))


def number(x):
    return Decimal(str(x or 0))




def release_stale_fbs_holds(db, c, w, ids):
    """Release only provably stale reservation rows before an operator FBS removal.

    Never touches a live NEW/PICKING order. Safe cleanup is limited to orphan
    reservations, cancelled-before-pack orders, and already-accounted packing rows.
    """
    ids=[str(x) for x in ids or [] if str(x or '').strip()]
    if not ids:return {'released':0,'consumed':0}
    from sqlalchemy import bindparam
    params={'c':str(c),'w':str(w),'ids':ids}
    released=db.execute(text('''
        UPDATE fc_stock_reservations r
        SET status='RELEASED',released_at=COALESCE(released_at,CURRENT_TIMESTAMP),
            updated_at=CURRENT_TIMESTAMP,
            note=CONCAT('AUTO_RELEASE_STALE_FBS_REMOVE; ',COALESCE(note,''))
        WHERE r.client_id=:c AND r.warehouse_id=:w AND r.sku_id IN :ids
          AND r.status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING')
          AND (
            NOT EXISTS (SELECT 1 FROM marketplace_orders o WHERE CAST(o.id AS varchar)=r.order_id)
            OR EXISTS (SELECT 1 FROM marketplace_orders o WHERE CAST(o.id AS varchar)=r.order_id
                       AND o.local_status='CANCELLED' AND o.packed_at IS NULL)
          )
    ''').bindparams(bindparam('ids',expanding=True)),params).rowcount or 0
    consumed=db.execute(text('''
        UPDATE fc_stock_reservations r
        SET status='CONSUMED',consumed_at=COALESCE(consumed_at,CURRENT_TIMESTAMP),
            updated_at=CURRENT_TIMESTAMP,
            note=CONCAT('AUTO_CLOSE_ACCOUNTED_FBS_REMOVE; ',COALESCE(note,''))
        WHERE r.client_id=:c AND r.warehouse_id=:w AND r.sku_id IN :ids
          AND r.status='PACKING_WRITE_OFF'
          AND EXISTS (
            SELECT 1 FROM marketplace_orders o
            JOIN inventory_operations io
              ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar)
             AND COALESCE(io.status,'COMPLETED')='COMPLETED'
             AND io.idempotency_key=('stock-core-v2-pack-'||CAST(o.id AS varchar)||'-'||CAST(r.id AS varchar))
            WHERE CAST(o.id AS varchar)=r.order_id
          )
    ''').bindparams(bindparam('ids',expanding=True)),params).rowcount or 0
    for key in list(db.info):
        if isinstance(key,tuple) and key and key[0]=='fc_active_order_floor':
            db.info.pop(key,None)
    return {'released':int(released),'consumed':int(consumed)}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def locks(db, c, w, ids):
    from app.services.stock_core_v2 import canonical_stock_identity_lock_key
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
               {'key': f'fulfillcore:picking-stock:{c}:{w}'})
    lock_scope(db, c, w)
    for key in sorted({canonical_stock_identity_lock_key(db, client_id=c, sku_id=s) for s in ids}):
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
                   {'key': f'fulfillcore:sale-reserve:{c}:{w}:{key}'})


def place(db, c, w, loc, box):
    row = db.execute(text('''SELECT CAST(id AS text) id, code FROM locations
        WHERE id=:loc AND warehouse_id=:w AND is_active=TRUE'''), {'loc':loc,'w':w}).mappings().first()
    if not row:
        raise HTTPException(404, detail='Ячейка не найдена на выбранном складе')
    name = ''
    if box:
        b = db.execute(text('''SELECT name,barcode,CAST(location_id AS text) location_id FROM containers
            WHERE id=:box AND client_id=:c AND warehouse_id=:w AND status='ACTIVE' FOR UPDATE'''),
            {'box':box,'c':c,'w':w}).mappings().first()
        if not b or str(b['location_id']) != str(loc):
            raise HTTPException(409, detail='Короб перемещён, архивный или относится к другому клиенту. Выберите место заново')
        name = b['name'] or b['barcode']
    return {'location_id':str(loc), 'container_id':str(box) if box else None,
            'location':row['code'], 'container':name}


def bucket(db, c, w, s, loc, box, read_only=False):
    p = {'c':str(c),'w':str(w),'s':str(s),'loc':str(loc),'box':str(box or '')}
    b = db.execute(text('''SELECT quantity,version FROM inventory_balances
        WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND COALESCE(CAST(container_id AS text),'')=:box''' + ('' if read_only else ' FOR UPDATE')), p).mappings().first()
    sv = db.execute(text('''SELECT storage_only,fbs_available,reserved,version FROM fc_stocks_v2
        WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND container_key=:box''' + ('' if read_only else ' FOR UPDATE')), p).mappings().first()
    physical = max(Decimal(0), number(b['quantity'] if b else 0))
    main = max(Decimal(0), min(number(sv['storage_only']), physical-number(sv['fbs_available'])-number(sv['reserved']))) if sv else Decimal(0)
    holds = number(db.execute(text('''SELECT COALESCE(SUM(CASE
          WHEN o.local_status IN ('NEW','PICKING') THEN r.quantity
          WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
               AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL)
               AND o.id IS NOT NULL THEN r.quantity
          ELSE 0 END),0)
        FROM fc_stock_reservations r
        LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
        LEFT JOIN inventory_operations io
          ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar)
         AND CAST(io.warehouse_id AS varchar)=CAST(r.warehouse_id AS varchar)
         AND COALESCE(io.status,'COMPLETED')='COMPLETED'
         AND io.idempotency_key=('stock-core-v2-pack-'||CAST(o.id AS varchar)||'-'||CAST(r.id AS varchar))
        WHERE r.client_id=:c AND r.warehouse_id=:w AND r.sku_id=:s
          AND r.source_location_id=:loc AND COALESCE(r.source_container_id,'')=:box
          AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')'''), p).scalar())
    external = number(db.execute(text('''SELECT COALESCE(SUM(quantity),0) FROM fc_external_pick_allocations
        WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND COALESCE(container_id,'')=:box AND status='ACTIVE' '''), p).scalar())
    main_held = held_quantity(db,c,w,s,loc,box)
    return {'sku_id':str(s),'location_id':str(loc),'container_id':str(box) if box else None,
            'physical':physical,'main':main,'storage_raw':number(sv['storage_only']) if sv else Decimal(0),'fbs':physical-main,'fbs_held':holds+external,
            'reserved':holds,'main_held':main_held,'version':int(b['version']) if b else 0,
            'mirror_version':int(sv['version']) if sv else 0}


def save_mirror(db,c,w,b,physical,main):
    """Set classification from the captured exact bucket, never consume the other class."""
    if physical < 0 or main < 0 or main > physical:
        raise HTTPException(409, detail='Остаток изменён одновременно. Повторите проверку')
    reserved = min(max(Decimal(0), b['reserved']), physical-main)
    db.execute(text('''INSERT INTO fc_stocks_v2(client_id,warehouse_id,sku_id,location_id,container_key,
        fbs_available,storage_only,reserved,physical_snapshot)
      VALUES(:c,:w,:s,:loc,:box,:fbs,:main,:reserved,:physical)
      ON CONFLICT(client_id,warehouse_id,sku_id,location_id,container_key) DO UPDATE SET
        fbs_available=EXCLUDED.fbs_available,storage_only=EXCLUDED.storage_only,reserved=EXCLUDED.reserved,
        physical_snapshot=EXCLUDED.physical_snapshot,version=fc_stocks_v2.version+1,updated_at=now()'''),
        {'c':str(c),'w':str(w),'s':b['sku_id'],'loc':b['location_id'],'box':b['container_id'] or '',
         'fbs':physical-main-reserved,'main':main,'reserved':reserved,'physical':physical})


def build_preview(db,c,w,req):
    c,w=str(c),str(w)
    ids = sorted(set(str(x) for x in req['sku_ids']))
    if not ids or len(ids)>100:
        raise HTTPException(422, detail='Выберите одну товарную позицию')
    locks(db,c,w,ids)
    products = []
    for s in ids:
        row = db.execute(text('''SELECT CAST(id AS text) id,barcode,article,size FROM skus
            WHERE id=:s AND client_id=:c AND is_active=TRUE'''), {'s':s,'c':c}).mappings().first()
        if not row:raise HTTPException(404, detail='Товар не найден у выбранного клиента')
        products.append(dict(row))
    if len({r['barcode'] or r['id'] for r in products})!=1:
        raise HTTPException(422, detail='Действие предназначено для одной товарной позиции')
    action,kind = req['action'],req['stock_class']
    if kind=='FBS' and action in ('REMOVE','REMOVE_ALL'):
        release_stale_fbs_holds(db,c,w,ids)
    candidates=[]
    if action=='REMOVE_ALL':
        for s in ids:
            candidates.extend(dict(r) for r in db.execute(text('''SELECT CAST(sku_id AS text) sku_id,
              CAST(location_id AS text) location_id,CAST(container_id AS text) container_id
              FROM inventory_balances WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND quantity>0
              ORDER BY location_id,container_id NULLS FIRST'''), {'c':c,'w':w,'s':s}).mappings().all())
    else:
        if len(ids)!=1 or not req.get('location_id'):
            raise HTTPException(422, detail='Выберите конкретное место товара')
        candidates=[{'sku_id':ids[0],'location_id':req['location_id'],'container_id':req.get('container_id')}]
    snapshots=[];lines=[];total=Decimal(0)
    for x in candidates:
        b=bucket(db,c,w,x['sku_id'],x['location_id'],x['container_id'])
        if b['storage_raw']!=b['main']:
            raise HTTPException(409,detail='Учёт Основного склада не совпадает с физическим остатком. Сначала нужна сверка этого адреса')
        qty=b['main' if kind=='MAIN' else 'fbs'] if action=='REMOVE_ALL' else number(req.get('quantity'))
        if qty<=0:continue
        if qty!=qty.to_integral_value():raise HTTPException(409, detail='Проверьте дробный остаток перед операцией')
        p=place(db,c,w,x['location_id'],x['container_id'])
        free=max(Decimal(0),b['main']-b['main_held']) if kind=='MAIN' else max(Decimal(0),b['fbs']-b['fbs_held'])
        if action!='ADD' and qty>free:
            raise HTTPException(409, detail=f"{p['container'] or p['location']}: свободно {free:g} шт. Есть резерв или незавершённое списание. Сначала завершите списание либо отмените действующее задание")
        snapshots.append(b);lines.append({**p,'sku_id':x['sku_id'],'quantity':int(qty),'before':float(b['main' if kind=='MAIN' else 'fbs'])});total+=qty
    if not lines:raise HTTPException(409, detail='В выбранном разделе нет остатка для этого действия')
    # Warehouse-level sale promises may not yet have a physical source address.
    if kind=='FBS' and action in ('REMOVE','REMOVE_ALL'):
        from app.services.stock_core_v2 import effective_fbs_free_for_sku
        if total>number(effective_fbs_free_for_sku(db,client_id=c,warehouse_id=w,sku_id=ids[0])):
            raise HTTPException(409, detail='Часть товара зарезервирована под заказы WB. Свободного количества для списания недостаточно')
    destination=None
    if action=='MOVE':
        if not req.get('destination_location_id'):raise HTTPException(422, detail='Укажите место назначения')
        destination=place(db,c,w,req['destination_location_id'],req.get('destination_container_id'))
        if (destination['location_id'],destination['container_id'])==(lines[0]['location_id'],lines[0]['container_id']):
            raise HTTPException(422, detail='Выберите другое место назначения')
        dest_bucket=bucket(db,c,w,ids[0],destination['location_id'],destination['container_id'])
        if dest_bucket['storage_raw']!=dest_bucket['main']:raise HTTPException(409,detail='Остаток места назначения требует сверки')
        snapshots.append(dest_bucket)
    return {'action':action,'stock_class':kind,'products':products,'lines':lines,'destination':destination,
            'quantity':int(total),'reason':req['reason'],'snapshot':snapshots}


def create_preview(db,c,w,req,actor):
    from app.services.stock_core_v2 import ensure_stock_core_v2_schema
    ensure_stock_core_v2_schema(db)
    preview=build_preview(db,c,w,req)
    digest=fingerprint(preview);plan_id=str(uuid.uuid4())
    db.execute(text('''INSERT INTO fc_stock_action_plans(id,client_id,warehouse_id,request_json,preview_json,fingerprint,actor)
      VALUES(:id,:c,:w,CAST(:req AS jsonb),CAST(:p AS jsonb),:hash,:actor)'''),
      {'id':plan_id,'c':str(c),'w':str(w),'req':json.dumps(req,default=str),'p':json.dumps(preview,default=str),'hash':digest,'actor':actor})
    return {'id':plan_id,'fingerprint':digest,'status':'PREVIEW',**{k:v for k,v in preview.items() if k!='snapshot'}}


def execute_ledger(db,c,w,action,lines,key,reason,actor):
    from app.schemas.inventory import OperationCreate,StockLine
    from app.services.inventory import execute_operation
    op=execute_operation(db,OperationCreate(client_id=c,warehouse_id=w,
        operation_type='MOVE' if action=='MOVE' else 'ADJUSTMENT',idempotency_key=key,
        performed_by=actor,reference='Ручное изменение остатка',comment=reason,
        lines=[StockLine(**x) for x in lines]),commit=False)
    return str(op.id)


def confirm(db,c,w,plan_id,digest,actor):
    from app.services.stock_core_v2 import ensure_stock_core_v2_schema
    ensure_stock_core_v2_schema(db)
    plan=db.execute(text('''SELECT * FROM fc_stock_action_plans
        WHERE id=:id AND client_id=:c AND warehouse_id=:w FOR UPDATE'''),
        {'id':str(plan_id),'c':str(c),'w':str(w)}).mappings().first()
    if not plan:raise HTTPException(404, detail='План не найден у выбранного клиента на этом складе')
    if plan['fingerprint']!=digest:raise HTTPException(409, detail='План изменён. Проверьте его заново')
    if plan['status']=='POSTED':
        current=db.execute(text('SELECT status FROM inventory_operations WHERE CAST(id AS text)=:id'),{'id':plan['operation_id']}).scalar()
        if current!='COMPLETED':raise HTTPException(409,detail='Эта операция отменена или отсутствует. Повторное проведение остановлено')
        return {'ok':True,'status':'POSTED','operation_id':plan['operation_id'],'replayed':True}
    req=plan['request_json']
    if req.get('action') not in ('ADD','REMOVE','REMOVE_ALL','MOVE'):raise HTTPException(409,detail='Другой тип операции')
    preview=build_preview(db,c,w,req)
    if fingerprint(preview)!=digest:raise HTTPException(409, detail='Остаток, адрес или резерв изменился. Обновите предпросмотр')
    action,kind=preview['action'],preview['stock_class'];ledger=[];changes=[]
    for i,row in enumerate(preview['lines']):
        b=preview['snapshot'][i];qty=number(row['quantity']);delta=qty if action=='ADD' else -qty
        line={'sku_id':uuid.UUID(row['sku_id']),'quantity':qty,'reason':req['reason'][:255]}
        if action!='ADD':line.update(source_location_id=uuid.UUID(row['location_id']),source_container_id=uuid.UUID(row['container_id']) if row['container_id'] else None)
        if action=='ADD':line.update(destination_location_id=uuid.UUID(row['location_id']),destination_container_id=uuid.UUID(row['container_id']) if row['container_id'] else None)
        if action=='MOVE':
            d=preview['destination'];line.update(destination_location_id=uuid.UUID(d['location_id']),destination_container_id=uuid.UUID(d['container_id']) if d['container_id'] else None)
        ledger.append(line);changes.append((b,b['physical']+delta,b['main']+(delta if kind=='MAIN' else 0)))
    if action=='MOVE':
        b=preview['snapshot'][-1];qty=number(preview['quantity']);changes.append((b,b['physical']+qty,b['main']+(qty if kind=='MAIN' else 0)))
    op=execute_ledger(db,c,w,action,ledger,'r69-stock-action-'+str(plan_id),req['reason'],actor)
    for b,physical,main in changes:
        actual=bucket(db,c,w,b['sku_id'],b['location_id'],b['container_id'])
        if actual['physical']!=physical:raise HTTPException(409, detail='Физическое изменение не подтверждено. Операция отменена')
        save_mirror(db,c,w,b,physical,main)
    db.execute(text("UPDATE fc_stock_action_plans SET status='POSTED',operation_id=:op,confirmed_at=now() WHERE id=:id"),{'op':op,'id':str(plan_id)})
    return {'ok':True,'status':'POSTED','operation_id':op,'quantity':preview['quantity'],'stock_class':kind}


def fast_remove_apply(db,c,w,req,actor):
    """Fast exact-place REMOVE path with the existing ledger and reservation guards."""
    from app.services.stock_core_v2 import ensure_stock_core_v2_schema
    ensure_stock_core_v2_schema(db)
    c,w=str(c),str(w)
    rid=str(req.get('request_id') or '')
    clean={k:v for k,v in req.items() if k!='request_id'}
    ids=[str(x) for x in clean.get('sku_ids') or []]
    if clean.get('action')!='REMOVE' or len(ids)!=1 or not clean.get('location_id'):
        raise HTTPException(422,detail='Быстрое списание доступно только для одного точного места товара')
    if clean.get('stock_class') not in ('MAIN','FBS'):
        raise HTTPException(422,detail='Неизвестный раздел склада')
    qty=number(clean.get('quantity'))
    if qty<=0 or qty!=qty.to_integral_value():
        raise HTTPException(422,detail='Укажите целое количество для списания')
    if not rid:
        raise HTTPException(422,detail='Не передан идентификатор операции')

    # One lock set for the whole request. The old path built the same preview twice.
    locks(db,c,w,ids)
    request_hash=fingerprint(clean)
    previous=db.execute(text('''SELECT * FROM fc_stock_action_plans
        WHERE id=:id FOR UPDATE'''),{'id':rid}).mappings().first()
    idem='r69-stock-action-'+rid
    if previous:
        if str(previous['client_id'])!=c or str(previous['warehouse_id'])!=w or fingerprint(previous['request_json'])!=request_hash:
            raise HTTPException(409,detail='Идентификатор списания уже использован с другими данными')
        if previous['status']=='POSTED':
            current=db.execute(text('SELECT status FROM inventory_operations WHERE CAST(id AS text)=:id'),{'id':previous['operation_id']}).scalar()
            if current!='COMPLETED':
                raise HTTPException(409,detail='Эта операция отменена или отсутствует. Повторное проведение остановлено')
            preview=previous['preview_json'] or {}
            return {'ok':True,'status':'POSTED','operation_id':str(previous['operation_id']),
                    'quantity':int(preview.get('quantity') or qty),'stock_class':clean['stock_class'],
                    'replayed':True,'fast_path':True}
        existing=db.execute(text('''SELECT CAST(id AS text) id,status FROM inventory_operations
            WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
              AND idempotency_key=:k ORDER BY created_at DESC LIMIT 1'''),
            {'c':c,'w':w,'k':idem}).mappings().first()
        if existing and existing['status']=='COMPLETED':
            preview=previous['preview_json'] or {}
            db.execute(text("UPDATE fc_stock_action_plans SET status='POSTED',operation_id=:op,confirmed_at=COALESCE(confirmed_at,now()) WHERE id=:id"),
                       {'op':existing['id'],'id':rid})
            return {'ok':True,'status':'POSTED','operation_id':existing['id'],
                    'quantity':int(preview.get('quantity') or qty),'stock_class':clean['stock_class'],
                    'replayed':True,'fast_path':True}
        if existing and existing['status']!='COMPLETED':
            raise HTTPException(409,detail='Предыдущее списание с этим идентификатором не завершено. Проверьте историю склада')

    sid=ids[0]
    product=db.execute(text('''SELECT CAST(id AS text) id,barcode,article,size FROM skus
        WHERE id=:s AND client_id=:c AND is_active=TRUE'''),{'s':sid,'c':c}).mappings().first()
    if not product:
        raise HTTPException(404,detail='Товар не найден у выбранного клиента')
    kind=clean['stock_class']
    if kind=='FBS':
        release_stale_fbs_holds(db,c,w,ids)

    b=bucket(db,c,w,sid,clean['location_id'],clean.get('container_id'))
    if b['storage_raw']!=b['main']:
        raise HTTPException(409,detail='Учёт Основного склада не совпадает с физическим остатком. Сначала нужна сверка этого адреса')
    p=place(db,c,w,clean['location_id'],clean.get('container_id'))
    free=max(Decimal(0),b['main']-b['main_held']) if kind=='MAIN' else max(Decimal(0),b['fbs']-b['fbs_held'])
    if qty>free:
        raise HTTPException(409,detail=f"{p['container'] or p['location']}: свободно {free:g} шт. Есть резерв или незавершённое списание")
    if kind=='FBS':
        from app.services.stock_core_v2 import effective_fbs_free_for_sku
        if qty>number(effective_fbs_free_for_sku(db,client_id=c,warehouse_id=w,sku_id=sid)):
            raise HTTPException(409,detail='Часть товара зарезервирована под заказы WB. Свободного количества для списания недостаточно')

    expected_physical=b['physical']-qty
    expected_main=b['main']-(qty if kind=='MAIN' else Decimal(0))
    preview={'action':'REMOVE','stock_class':kind,'quantity':int(qty),'sku_id':sid,
             'location_id':str(clean['location_id']),'container_id':str(clean.get('container_id')) if clean.get('container_id') else None,
             'article':product.get('article'),'size':product.get('size'),'barcode':product.get('barcode'),
             'before_physical':float(b['physical']),'after_physical':float(expected_physical),
             'before_class':float(b['main'] if kind=='MAIN' else b['fbs'])}
    if previous:
        db.execute(text('''UPDATE fc_stock_action_plans SET request_json=CAST(:req AS jsonb),preview_json=CAST(:p AS jsonb),
            fingerprint=:hash,status='PREVIEW',actor=:actor WHERE id=:id'''),
            {'req':json.dumps(clean,default=str),'p':json.dumps(preview,default=str),'hash':request_hash,'actor':actor,'id':rid})
    else:
        db.execute(text('''INSERT INTO fc_stock_action_plans(id,client_id,warehouse_id,request_json,preview_json,fingerprint,status,actor)
            VALUES(:id,:c,:w,CAST(:req AS jsonb),CAST(:p AS jsonb),:hash,'PREVIEW',:actor)'''),
            {'id':rid,'c':c,'w':w,'req':json.dumps(clean,default=str),'p':json.dumps(preview,default=str),'hash':request_hash,'actor':actor})

    line={'sku_id':uuid.UUID(sid),'quantity':qty,'reason':str(clean.get('reason') or 'Ручное списание')[:255],
          'source_location_id':uuid.UUID(str(clean['location_id'])),
          'source_container_id':uuid.UUID(str(clean['container_id'])) if clean.get('container_id') else None}
    op=execute_ledger(db,c,w,'REMOVE',[line],idem,str(clean.get('reason') or 'Ручное списание'),actor)

    # Verify only the physical row; repeating bucket() here would re-run hold queries.
    params={'c':c,'w':w,'s':sid,'loc':str(clean['location_id']),'box':str(clean.get('container_id') or '')}
    actual_raw=db.execute(text('''SELECT quantity FROM inventory_balances
        WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND COALESCE(CAST(container_id AS text),'')=:box'''),params).scalar()
    actual=max(Decimal(0),number(actual_raw))
    if actual!=expected_physical:
        raise HTTPException(409,detail='Физическое списание не подтвердилось. Операция отменена')
    save_mirror(db,c,w,b,expected_physical,expected_main)
    db.execute(text("UPDATE fc_stock_action_plans SET status='POSTED',operation_id=:op,confirmed_at=now() WHERE id=:id"),
               {'op':op,'id':rid})
    return {'ok':True,'status':'POSTED','operation_id':op,'quantity':int(qty),'stock_class':kind,
            'replayed':False,'fast_path':True}

def bulk_remove(db,c,w,req,actor):
    """All selected places commit together; replay never removes stock twice."""
    c,w=str(c),str(w);rid=str(req['request_id']);digest=fingerprint(req)
    locks(db,c,w,sorted({str(x['sku_id']) for x in req['items']}))
    previous=db.execute(text('SELECT * FROM fc_stock_action_plans WHERE id=:id FOR UPDATE'),{'id':rid}).mappings().first()
    if previous:
        if previous['client_id']!=c or previous['warehouse_id']!=w or previous['fingerprint']!=digest:raise HTTPException(409,detail='Идентификатор списания уже использован с другими данными')
        result=dict(previous['preview_json'])
        for op in result['operation_ids']:
            if db.execute(text('SELECT status FROM inventory_operations WHERE CAST(id AS text)=:id'),{'id':op}).scalar()!='COMPLETED':raise HTTPException(409,detail='Одно из списаний отменено. Повторное проведение остановлено')
        return {**result,'replayed':True}
    identities=[(str(x['sku_id']),str(x['location_id']),str(x.get('container_id') or '')) for x in req['items']]
    if len(set(identities))!=len(identities):raise HTTPException(422,detail='Одно место товара указано дважды')
    results=[]
    for x in req['items']:
        one={'action':'REMOVE','stock_class':req['stock_class'],'sku_ids':[str(x['sku_id'])],
             'location_id':str(x['location_id']),'container_id':str(x['container_id']) if x.get('container_id') else None,
             'quantity':x['quantity'],'reason':req['reason']}
        plan=create_preview(db,c,w,one,actor)
        results.append(confirm(db,c,w,plan['id'],plan['fingerprint'],actor))
    result={'ok':True,'quantity':sum(x['quantity'] for x in results),'operation_ids':[str(x['operation_id']) for x in results],'stock_class':req['stock_class'],'replayed':False}
    db.execute(text('''INSERT INTO fc_stock_action_plans(id,client_id,warehouse_id,request_json,preview_json,fingerprint,status,actor,confirmed_at)
      VALUES(:id,:c,:w,CAST(:req AS jsonb),CAST(:result AS jsonb),:hash,'POSTED',:actor,now())'''),
      {'id':rid,'c':c,'w':w,'req':json.dumps(req),'result':json.dumps(result),'hash':digest,'actor':actor})
    return result
