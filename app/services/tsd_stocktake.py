"""Durable TSD counting/receiving drafts over the existing stock ledger.

Scans edit a draft. Posting is one transaction, with replay protection, exact
place checks and an optimistic snapshot for counts. Reads never install DDL.
"""
import copy
import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from app.models import Container, InventoryBalance, InventoryOperation, Location, SKU
from app.services import stock_actions as stock
from app.services.local_catalog import catalog
from app.services.local_catalog_rules import barcode as product_barcode
from app.services.box_intake_rules import box_code, normalize_box_scan


def install_schema(db):
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_tsd_work (
        id varchar(36) PRIMARY KEY, client_id varchar(36) NOT NULL,
        warehouse_id varchar(36) NOT NULL, owner text NOT NULL, picker varchar(120) NOT NULL,
        mode varchar(12) NOT NULL CHECK(mode IN ('COUNT','RECEIVE')),
        status varchar(12) NOT NULL DEFAULT 'DRAFT', version integer NOT NULL DEFAULT 0,
        request_hash varchar(64) NOT NULL, data jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())'''))
    db.execute(text('''CREATE INDEX IF NOT EXISTS ix_fc_tsd_work_scope
        ON fc_tsd_work(client_id,warehouse_id,owner,picker,status,updated_at DESC)'''))
    db.execute(text('''CREATE TABLE IF NOT EXISTS fc_tsd_work_events (
        id varchar(36) PRIMARY KEY, work_id varchar(36) NOT NULL REFERENCES fc_tsd_work(id),
        request_hash varchar(64) NOT NULL, action varchar(12) NOT NULL,
        payload jsonb NOT NULL, actor text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())'''))


def raw_codes(raw):
    value = str(raw or '').strip()
    if not value or len(value) > 255 or any(ord(x) < 32 for x in value):
        raise HTTPException(422, detail='Сканируйте код короба или адреса')
    return {value.lower(), normalize_box_scan(value).lower()}


def resolve_location(db, w, raw, kinds=None):
    codes = raw_codes(raw)
    rows = db.execute(select(Location).where(Location.warehouse_id == uuid.UUID(str(w)),
        Location.is_active.is_(True), or_(func.lower(Location.code).in_(codes),
        func.lower(Location.barcode).in_(codes)))).scalars().all()
    if len(rows) != 1:
        raise HTTPException(409 if rows else 404, detail='Адрес неоднозначен' if rows else 'Адрес не найден на выбранном складе')
    loc = rows[0]
    if kinds and loc.location_type not in kinds:
        raise HTTPException(422, detail='Отсканирован другой тип адреса')
    return loc


def target(db, c, w, code, kind='AUTO'):
    codes = raw_codes(code)
    boxes = [] if kind == 'LOCATION' else db.execute(select(Container).where(
        Container.client_id == uuid.UUID(str(c)), Container.warehouse_id == uuid.UUID(str(w)),
        Container.status == 'ACTIVE', or_(func.lower(Container.name).in_(codes),
        func.lower(Container.barcode).in_(codes)))).scalars().all()
    places = [] if kind == 'BOX' else db.execute(select(Location).where(
        Location.warehouse_id == uuid.UUID(str(w)), Location.is_active.is_(True),
        or_(func.lower(Location.code).in_(codes), func.lower(Location.barcode).in_(codes)))).scalars().all()
    if len(boxes) + len(places) != 1:
        raise HTTPException(409 if boxes or places else 404, detail=(
            'Код совпал у короба и адреса. Выберите тип: короб или ячейка / палета.'
            if boxes or places else 'Короб или адрес не найден. Сначала создайте или примите короб либо проверьте адрес.'))
    box = boxes[0] if boxes else None
    loc = db.get(Location, box.location_id) if box and box.location_id else places[0] if places else None
    if box and loc and (not loc.is_active or str(loc.warehouse_id) != str(w)):
        raise HTTPException(409, detail='Адрес короба архивный или относится к другому складу')
    if not box and loc.location_type in ('SECTION', 'RACK'):
        raise HTTPException(422, detail='Выбрана секция или стеллаж. Для товара сканируйте конкретный короб, ячейку или палету.')
    return box, loc



def address(db, c, w, box, loc):
    if not loc:
        raise HTTPException(409, detail='Короб ещё не размещён. Укажите палету / ячейку и включите «Разместить пустой короб».')
    ancestors = []
    seen = set()
    node = loc
    while node and str(node.id) not in seen:
        if str(node.warehouse_id) != str(w) or not node.is_active:
            raise HTTPException(409, detail='Адресная цепочка содержит чужой или архивный адрес')
        seen.add(str(node.id))
        ancestors.append({'id': str(node.id), 'code': node.code, 'type': node.location_type})
        node = db.get(Location, node.parent_id) if node.parent_id else None
    route = db.execute(text('''SELECT row_on_pallet,row_position FROM fc_box_routes
        WHERE container_id=:b AND client_id=:c AND warehouse_id=:w AND location_id=:l'''),
        {'b':str(box.id) if box else '', 'c':str(c), 'w':str(w), 'l':str(loc.id)}).mappings().first()
    unplaced = bool(box and str(loc.code or '').strip().upper() in {'ПРИЕМКА','UNPLACED','БЕЗ-АДРЕСА'})
    return {'location_id':str(loc.id), 'container_id':str(box.id) if box else None,
        'location':loc.code, 'container':(box.name or box.barcode) if box else '',
        'barcode':box.barcode if box else loc.barcode, 'ancestors':ancestors,
        'row':int(route['row_on_pallet']) if route else None,
        'position':int(route['row_position']) if route else None, 'unplaced':unplaced}


def current_place(db, c, w, saved, allow_box_move=False):
    # COUNT stays bound to the exact counted place. RECEIVE follows the active
    # box identity because a valid box may be moved to a pallet before posting.
    box_id = str((saved or {}).get('container_id') or '')
    if allow_box_move and box_id:
        live = db.execute(text('''SELECT CAST(id AS text) id, CAST(location_id AS text) location_id
            FROM containers WHERE id=CAST(:box AS uuid) AND client_id=CAST(:c AS uuid)
              AND warehouse_id=CAST(:w AS uuid) AND status='ACTIVE' FOR SHARE'''),
            {'box':box_id,'c':str(c),'w':str(w)}).mappings().first()
        if not live:
            raise HTTPException(409, detail='Короб архивный или относится к другому клиенту / складу. Проверьте короб перед завершением приёмки.')
        if not live['location_id']:
            raise HTTPException(409, detail='Короб существует, но сейчас не размещён. Разместите его на палету / ячейку и повторите завершение приёмки.')
        box = db.get(Container, uuid.UUID(live['id']))
        loc = db.get(Location, uuid.UUID(live['location_id']))
        return address(db, c, w, box, loc)
    stock.place(db, str(c), str(w), saved['location_id'], saved['container_id'])
    loc = db.get(Location, uuid.UUID(saved['location_id']))
    box = db.get(Container, uuid.UUID(saved['container_id'])) if saved['container_id'] else None
    return address(db, c, w, box, loc)


def capture(db, c, w, place):
    """Include zero rows/versions: a move away and back must invalidate a count."""
    rows = db.execute(text('''SELECT CAST(sku_id AS text) sku_id, quantity, version
        FROM inventory_balances WHERE client_id=:c AND warehouse_id=:w
        AND location_id=:l AND COALESCE(CAST(container_id AS text),'')=:b ORDER BY sku_id'''),
        {'c':str(c),'w':str(w),'l':place['location_id'],'b':place['container_id'] or ''}).mappings().all()
    if len(rows) > 1000:
        raise HTTPException(422, detail='В адресе больше 1000 позиций. Разделите пересчёт по коробам.')
    snapshots = []
    for r in rows:
        if r['quantity'] < 0:
            raise HTTPException(409, detail='В адресе найден отрицательный остаток. Нужна проверка учёта.')
        b = stock.bucket(db,c,w,r['sku_id'],place['location_id'],place['container_id'],read_only=True)
        snapshots.append({'sku_id':r['sku_id'], 'quantity':str(r['quantity']),
            'version':r['version'], 'main':str(b['main']), 'mirror_version':b['mirror_version']})
    return snapshots


def line_for(sku, expected=0, counted=None, bucket=None):
    if Decimal(str(expected)) != Decimal(str(expected)).to_integral_value():
        raise HTTPException(409, detail='В адресе есть дробный остаток. Проверьте его перед пересчётом.')
    b = bucket or {}
    return {'sku_id':str(sku.id), 'barcode':sku.barcode or '', 'article':sku.article or '',
        'size':sku.size or '', 'name':sku.name or '', 'expected':int(Decimal(str(expected))), 'quantity':counted,
        'main':int(b.get('main',0)), 'fbs':int(b.get('fbs',0)),
        'reserved':float(b.get('fbs_held',0) + b.get('main_held',0)), 'first_scanned_at':None, 'last_scanned_at':None}


def _effective_event_time(raw):
    """UTC factual operator time, with a safe fallback for broken device clocks."""
    now=datetime.now(timezone.utc)
    if raw:
        try:
            dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            dt=dt.astimezone(timezone.utc)
            if now-timedelta(days=30) <= dt <= now+timedelta(hours=6):
                return dt.isoformat().replace('+00:00','Z')
        except Exception:
            pass
    return now.isoformat().replace('+00:00','Z')


def _recalc_receive_times(data):
    lines=(data.get('lines') or {}).values()
    active=[x for x in lines if int(x.get('quantity') or 0)>0]
    first=[str(x.get('first_scanned_at') or '') for x in active if x.get('first_scanned_at')]
    last=[str(x.get('last_scanned_at') or '') for x in active if x.get('last_scanned_at')]
    data['first_item_at']=min(first) if first else None
    data['last_item_at']=max(last) if last else None


def _touch_received_line(data,line,when):
    if not line.get('first_scanned_at'):line['first_scanned_at']=when
    line['last_scanned_at']=when
    _recalc_receive_times(data)

def get(db, wid, c, w, owner, lock=False):
    row = db.execute(text('''SELECT * FROM fc_tsd_work WHERE id=:id AND client_id=:c
        AND warehouse_id=:w AND owner=:owner''' + (' FOR UPDATE' if lock else '')),
        {'id':str(wid),'c':str(c),'w':str(w),'owner':owner}).mappings().first()
    if not row:
        raise HTTPException(404, detail='Черновик ТСД не найден у выбранного клиента и склада')
    return {**dict(row), 'data':copy.deepcopy(row['data'])}


def public(row):
    data = row['data']
    lines = list(data['lines'].values())
    return {'id':row['id'],'mode':row['mode'],'status':row['status'],'version':row['version'],
        'picker':row['picker'], 'receipt_number':str(data.get('receipt_number') or ''), 'place':data['place'], 'lines':lines,
        'units':sum(x['quantity'] or 0 for x in lines), 'unchecked':sum(x['quantity'] is None for x in lines),
        'can_undo':bool(data.get('undo')), 'result':data.get('result'), 'first_item_at':data.get('first_item_at'), 'last_item_at':data.get('last_item_at'), 'updated_at':str(row['updated_at'])}


def resolve_work_place(db, c, w, req, picker, mode):
    """Resolve only an already existing active box/address. RECEIVE never creates boxes."""
    created_box = False
    restored_box = False
    try:
        box,loc = target(db,c,w,req['code'],req.get('target_type') or 'AUTO')
    except HTTPException as exc:
        if exc.status_code == 404 and mode == 'RECEIVE' and (req.get('target_type') or 'AUTO') != 'LOCATION':
            raise HTTPException(404, detail='Короб не найден. Сначала создайте или примите короб.')
        raise
    if mode == 'RECEIVE' and not box:
        raise HTTPException(422, detail='Для приёмки товара отсканируйте существующий короб. Сначала создайте или примите короб.')
    if req.get('placement_code'):
        placement = resolve_location(db,w,req['placement_code'],{'CELL','PALLET','PALLET_PLACE'})
        if box and (loc is None or placement.id != loc.id):
            if created_box or restored_box:
                box.location_id = placement.id
                db.flush(); loc = placement
            else:
                if mode != 'RECEIVE' or not req.get('place_empty_box'):
                    raise HTTPException(409, detail='Короб находится в другом месте. Для пустого короба включите «Разместить пустой короб».')
                from app.services.box_intake import empty_box_change
                empty_box_change(db,box.id,uuid.UUID(str(c)),uuid.UUID(str(w)),picker,destination=placement.id)
                db.expire(box);loc=placement
        elif not box and loc.id != placement.id:
            raise HTTPException(409, detail='Скан адреса не совпадает с указанной палетой / ячейкой')
    place = address(db,c,w,box,loc)
    place['created_box'] = created_box
    place['restored_box'] = restored_box
    if req.get('section_code'):
        section = resolve_location(db,w,req['section_code'],{'SECTION'})
        if str(section.id) not in {x['id'] for x in place['ancestors']}:
            raise HTTPException(409, detail='Выбранный короб / адрес находится в другой секции')
    if req.get('row') is not None:
        if not box or mode != 'RECEIVE':
            raise HTTPException(422, detail='Ряд задаётся для короба при приёмке')
        if place.get('unplaced'):
            raise HTTPException(422, detail='Ряд можно указать после выбора палеты / ячейки.')
        from app.services.box_routes import set_box_route
        set_box_route(db,container_id=box.id,client_id=c,warehouse_id=w,
            row_on_pallet=req['row'],actor=picker,reason='Размещение с ТСД')
        place = address(db,c,w,box,loc)
        place['created_box'] = created_box
        place['restored_box'] = restored_box
    return place,box,loc


def _count_lines_for_place(db,c,w,place,box,loc):
    baseline = capture(db,c,w,place)
    lines = {}
    for b in baseline:
        if Decimal(b['quantity']) <= 0: continue
        sku = db.get(SKU,uuid.UUID(b['sku_id']))
        if not sku or not sku.is_active or str(sku.client_id)!=str(c):
            raise HTTPException(409,detail='В адресе есть архивный или чужой товар. Проверьте справочник.')
        row = line_for(sku,b['quantity'],bucket=stock.bucket(db,c,w,sku.id,loc.id,box.id if box else None,read_only=True))
        lines[row['sku_id']] = row
    return baseline,lines


def _table_exists(db, name):
    return bool(db.execute(text("SELECT to_regclass(:n)"), {'n':'public.'+name}).scalar())


def history_rows(db, c, w, limit=200):
    limit=max(1,min(int(limit or 200),1000))
    out=[]
    if _table_exists(db,'fc_tsd_work'):
        rows=db.execute(text("""SELECT id,owner,picker,mode,status,data,created_at,updated_at
            FROM fc_tsd_work WHERE client_id=:c AND warehouse_id=:w AND status<>'DRAFT'
            ORDER BY updated_at DESC LIMIT :lim"""),{'c':str(c),'w':str(w),'lim':limit}).mappings().all()
        for r in rows:
            data=r['data'] if isinstance(r['data'],dict) else json.loads(r['data'] or '{}')
            place=data.get('place') or {}; lines=list((data.get('lines') or {}).values())
            out.append({'id':str(r['id']),'kind':str(r['mode']),'status':str(r['status']),
                'picker':str(r['picker'] or ''),'owner':str(r['owner'] or ''),
                'created_at':r['created_at'],'updated_at':r['updated_at'],
                'place':place.get('container') or place.get('location') or '',
                'address':place.get('location') or '',
                'units':sum(int(x.get('quantity') or 0) for x in lines),
                'receipt_number':str(data.get('receipt_number') or ''),
                'first_item_at':data.get('first_item_at'),'last_item_at':data.get('last_item_at'),
                'items':[{'barcode':x.get('barcode') or '', 'article':x.get('article') or '', 'size':x.get('size') or '', 'name':x.get('name') or '', 'quantity':int(x.get('quantity') or 0), 'first_scanned_at':x.get('first_scanned_at'), 'last_scanned_at':x.get('last_scanned_at')} for x in lines if int(x.get('quantity') or 0)>0],
                'reference':(('Приёмка '+str(data.get('receipt_number') or '').strip()) if r['mode']=='RECEIVE' and str(data.get('receipt_number') or '').strip() else ('Приёмка товара ТСД' if r['mode']=='RECEIVE' else 'Инвентаризация ТСД')),
                'operation_id':str((data.get('result') or {}).get('operation_id') or '')})
    move_rows=db.execute(text("""SELECT CAST(o.id AS text) id,o.created_at,o.status,o.performed_by,o.comment,
               COALESCE(SUM(l.quantity),0) units,MIN(l.reason) reason
        FROM inventory_operations o JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE o.client_id=CAST(:c AS uuid) AND o.warehouse_id=CAST(:w AS uuid) AND l.reason LIKE 'Перемещение ТСД · %'
        GROUP BY o.id,o.created_at,o.status,o.performed_by,o.comment
        ORDER BY o.created_at DESC LIMIT :lim"""),{'c':str(c),'w':str(w),'lim':limit}).mappings().all()
    for r in move_rows:
        reason=str(r['reason'] or ''); picker=reason.split('·',1)[1].strip() if '·' in reason else str(r['performed_by'] or '')
        comment=str(r['comment'] or ''); owner=str(r['performed_by'] or '')
        marker='учётная запись:'
        if marker in comment.lower(): owner=comment[comment.lower().index(marker)+len(marker):].strip().split('·',1)[0].strip() or owner
        out.append({'id':r['id'],'kind':'MOVE','status':str(r['status'] or 'COMPLETED'),'picker':picker,
            'owner':owner,'created_at':r['created_at'],'updated_at':r['created_at'],
            'place':'Короб → короб','address':'','units':float(r['units'] or 0),'reference':'Перемещение ТСД','operation_id':r['id']})
    if _table_exists(db,'fc_wh_pick_tasks') and _table_exists(db,'fc_wh_pick_audit'):
        pick_rows=db.execute(text("""SELECT t.id,t.task_number,t.name,t.picker,t.status,t.created_by,t.created_at,
               COALESCE(t.closed_at,t.submitted_at,t.assigned_at,t.created_at) updated_at,
               COALESCE(SUM(l.scan_qty+l.manual_qty+l.missing_qty),0) units
          FROM fc_wh_pick_tasks t LEFT JOIN fc_wh_pick_lines l ON l.task_id=t.id
         WHERE t.client_id=:c AND t.warehouse_id=:w
           AND EXISTS(SELECT 1 FROM fc_wh_pick_audit a WHERE a.task_id=t.id AND a.action LIKE 'TSD_%')
         GROUP BY t.id,t.task_number,t.name,t.picker,t.status,t.created_by,t.created_at,t.closed_at,t.submitted_at,t.assigned_at
         ORDER BY updated_at DESC LIMIT :lim"""),{'c':str(c),'w':str(w),'lim':limit}).mappings().all()
        for r in pick_rows:
            out.append({'id':str(r['id']),'kind':'PICK','status':str(r['status'] or ''),'picker':str(r['picker'] or ''),
                'owner':str(r['created_by'] or ''),'created_at':r['created_at'],'updated_at':r['updated_at'],
                'place':'Сборочное задание','address':'','units':float(r['units'] or 0),
                'reference':str(r['task_number'] or r['name'] or 'Сборка ТСД'),'operation_id':''})
    out.sort(key=lambda x:x.get('updated_at') or x.get('created_at'),reverse=True)
    return out[:limit]


def history_events(db,c,w,limit=2000):
    limit=max(1,min(int(limit or 2000),100000)); out=[]
    if _table_exists(db,'fc_tsd_work_events'):
        rows=db.execute(text("""SELECT e.work_id,e.action,e.payload,e.actor,e.created_at,w.picker,w.mode,w.data
          FROM fc_tsd_work_events e JOIN fc_tsd_work w ON w.id=e.work_id
         WHERE w.client_id=:c AND w.warehouse_id=:w
         ORDER BY e.created_at DESC LIMIT :lim"""),{'c':str(c),'w':str(w),'lim':limit}).mappings().all()
        for r in rows:
            payload=r['payload'] if isinstance(r['payload'],dict) else json.loads(r['payload'] or '{}')
            data=r['data'] if isinstance(r['data'],dict) else json.loads(r['data'] or '{}')
            place=data.get('place') or {}; lines=data.get('lines') or {}
            code=str(payload.get('barcode') or '')
            sid=str(payload.get('sku_id') or '')
            item=(lines.get(sid) or {}) if sid else (next((x for x in lines.values() if str(x.get('barcode') or '')==code),{}) if code else {})
            if not code:code=str(item.get('barcode') or '')
            out.append({'id':str(r['work_id']),'kind':str(r['mode']),'action':str(r['action']),
                'picker':str(r['picker'] or ''),'owner':str(r['actor'] or ''),'created_at':r['created_at'],
                'occurred_at':payload.get('effective_at') or payload.get('occurred_at') or r['created_at'],
                'receipt_number':str(data.get('receipt_number') or ''),'place':place.get('container') or place.get('location') or '',
                'barcode':code,'article':item.get('article') or '','size':item.get('size') or '',
                'quantity':1 if str(r['action'])=='SCAN' else (payload.get('quantity') if payload.get('quantity') is not None else None),
                'details':json.dumps(payload,ensure_ascii=False,sort_keys=True)})
    if _table_exists(db,'fc_wh_pick_audit') and _table_exists(db,'fc_wh_pick_tasks'):
        rows=db.execute(text("""SELECT a.task_id,a.action,a.actor,a.reason,a.created_at,t.task_number
          FROM fc_wh_pick_audit a JOIN fc_wh_pick_tasks t ON t.id=a.task_id
         WHERE t.client_id=:c AND t.warehouse_id=:w AND (a.action LIKE 'TSD_%' OR a.action='SUBMIT_ADMIN')
         ORDER BY a.created_at DESC LIMIT :lim"""),{'c':str(c),'w':str(w),'lim':limit}).mappings().all()
        for r in rows:
            out.append({'id':str(r['task_id']),'kind':'PICK','action':str(r['action']),
                'picker':str(r['actor'] or ''),'owner':'','created_at':r['created_at'],
                'details':('Задание '+str(r['task_number'] or ''))+(' · '+str(r['reason']) if r['reason'] else '')})
    out.sort(key=lambda x:x.get('created_at'),reverse=True)
    return out[:limit]


def start(db, req, owner):
    c,w,rid = req['client_id'],req['warehouse_id'],req['request_id']
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k':'fc:tsd-start:'+rid})
    prior = db.execute(text('SELECT * FROM fc_tsd_work WHERE id=:id'),{'id':rid}).mappings().first()
    digest = stock.fingerprint(req)
    if prior:
        if prior['request_hash'] != digest or prior['owner'] != owner:
            raise HTTPException(409, detail='Этот запрос уже использован для другого черновика')
        return public(prior)
    stock.locks(db,c,w,[])
    place,box,loc = resolve_work_place(db,c,w,req,req['picker'],req['mode'])
    if req['mode']=='RECEIVE' and not str(req.get('receipt_number') or '').strip():
        raise HTTPException(422,detail='Перед приёмкой укажите номер или название приёмки')
    if req['mode']=='COUNT':
        baseline,lines = _count_lines_for_place(db,c,w,place,box,loc)
    else:
        baseline,lines = [],{}
    data = {'place':place,'baseline':baseline,'lines':lines,'undo':[],'receipt_number':str(req.get('receipt_number') or '').strip(),'first_item_at':None,'last_item_at':None}
    db.execute(text('''INSERT INTO fc_tsd_work(id,client_id,warehouse_id,owner,picker,mode,request_hash,data)
        VALUES(:id,:c,:w,:owner,:picker,:mode,:hash,CAST(:data AS jsonb))'''),
        {'id':rid,'c':c,'w':w,'owner':owner,'picker':req['picker'],'mode':req['mode'],
         'hash':digest,'data':json.dumps(data,ensure_ascii=False)})
    return public(get(db,rid,c,w,owner))


def _gtin_checksum_valid(code):
    if not str(code or '').isdigit() or len(str(code)) not in (8,12,13,14):
        return False
    digits=[int(x) for x in str(code)]
    body=digits[:-1]
    total=sum(n*(3 if i%2==0 else 1) for i,n in enumerate(reversed(body)))
    return ((10-total%10)%10)==digits[-1]


def _guard_new_tsd_barcode(db, c, w, code):
    # Existing legacy/local SKU codes are accepted before this function is called.
    # The guard applies only to a brand-new code that TSD is about to register.
    if len(set(code))==1 or not _gtin_checksum_valid(code):
        raise HTTPException(422,detail=(
            f'ШК {code} не прошёл контрольную цифру GTIN. Скан не принят и к товару не привязан. '
            'Повторно отсканируйте именно товарный штрихкод.'
        ))
    article_match=db.execute(select(SKU.id).where(
        SKU.client_id==uuid.UUID(str(c)),func.lower(func.trim(SKU.article))==code.casefold()
    ).limit(1)).scalar_one_or_none()
    if article_match:
        raise HTTPException(422,detail=(
            f'Код {code} уже встречается как артикул. Похоже, отсканирован артикул, а не ШК товара. '
            'Скан не принят.'
        ))
    box_match=db.execute(select(Container.id).where(
        Container.client_id==uuid.UUID(str(c)),Container.warehouse_id==uuid.UUID(str(w)),
        Container.status=='ACTIVE',or_(func.lower(Container.barcode)==code.casefold(),func.lower(Container.name)==code.casefold())
    ).limit(1)).scalar_one_or_none()
    loc_match=db.execute(select(Location.id).where(
        Location.warehouse_id==uuid.UUID(str(w)),Location.is_active.is_(True),
        or_(func.lower(Location.code)==code.casefold(),func.lower(Location.barcode)==code.casefold())
    ).limit(1)).scalar_one_or_none()
    if box_match or loc_match:
        raise HTTPException(422,detail=(
            f'Код {code} относится к коробу или адресу склада, а не к товару. Скан не принят.'
        ))


def product(db, c, w, raw, registration=None):
    try: code = product_barcode(raw)
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':f'fc:local-sku:{c}:{code}'})
    rows = catalog(db,uuid.UUID(str(c)),code)
    known = rows[0] if len(rows)==1 else None
    if known and (known.get('duplicate_sku') or known.get('wb_conflict')):
        raise HTTPException(409,detail='У штрихкода несколько карточек. Исправьте привязку в справочнике.')
    skus = db.execute(select(SKU).where(SKU.client_id==uuid.UUID(str(c)),SKU.barcode==code)).scalars().all()
    if len(skus)>1 or (skus and not skus[0].is_active):
        raise HTTPException(409,detail='Штрихкод неоднозначен или товар архивный')
    if skus: return skus[0]
    info = known if known and known.get('wb_found') else registration
    if known and known.get('wb_found'):
        pass
    else:
        _guard_new_tsd_barcode(db,c,w,code)
        if registration and not bool(registration.get('barcode_confirmed')):
            raise HTTPException(409,detail='Новый ШК должен быть повторно отсканирован тем же кодом перед созданием товара.')
    if not info or not str(info.get('article') or '').strip() or not str(info.get('size') or '').strip():
        raise HTTPException(404,detail={'code':'UNKNOWN_PRODUCT','barcode':code,
            'message':'Товар не найден. Для нового ШК отсканируйте этот же код ещё раз, затем укажите артикул и размер.'})
    sku = SKU(id=uuid.uuid4(),client_id=uuid.UUID(str(c)),sku_code='LOCAL-'+uuid.uuid5(uuid.UUID(str(c)),code).hex,
        barcode=code,article=str(info['article']).strip(),size=str(info['size']).strip(),
        name=str(info.get('name') or info['article']).strip(),unit='PCS',is_active=True)
    db.add(sku);db.flush()
    return sku


def post(db, row, owner):
    from app.schemas.inventory import OperationCreate, StockLine
    from app.services.inventory import execute_operation
    from app.services.stock_core_v2 import effective_fbs_free_for_sku, ensure_stock_core_v2_schema
    from app.routers.stock_actions import queue_fbs
    data,c,w=row['data'],row['client_id'],row['warehouse_id']
    items=data['lines']
    ensure_stock_core_v2_schema(db)
    stock.locks(db,c,w,items)
    place = current_place(db,c,w,data['place'],allow_box_move=(row['mode']=='RECEIVE'))
    if row['mode']=='RECEIVE':
        data['place']=place  # history must keep the actual final box address
    if row['mode']=='COUNT':
        if any(x['quantity'] is None for x in items.values()):
            raise HTTPException(409,detail='Проверьте каждую позицию. Если товара нет, нажмите «Нет · 0». Неотсканированное не списывается автоматически.')
        if stock.fingerprint(capture(db,c,w,place)) != stock.fingerprint(data['baseline']):
            raise HTTPException(409,detail='Остаток изменился во время пересчёта. Черновик сохранён. Начните новый пересчёт этого места; старые цифры не применены.')
    elif not items or not any(x['quantity'] for x in items.values()):
        raise HTTPException(409,detail='Сначала отсканируйте товар')
    ledger,changes,summary,fbs_ids = [],[],[],set()
    for sid,x in sorted(items.items()):
        sku = db.get(SKU,uuid.UUID(sid))
        if not sku or not sku.is_active or str(sku.client_id)!=c:
            raise HTTPException(409,detail='Товар архивный или изменён. Проверьте справочник.')
        b=stock.bucket(db,c,w,sid,place['location_id'],place['container_id'])
        if b['storage_raw']!=b['main']:
            raise HTTPException(409,detail='Физический остаток и учёт Основного склада расходятся. Нужна сверка адреса.')
        delta=Decimal(x['quantity']) if row['mode']=='RECEIVE' else Decimal(x['quantity'])-b['physical']
        main_after=b['main']+max(Decimal(0),delta)
        if delta<0:
            main_remove=min(-delta,max(Decimal(0),b['main']-b['main_held']))
            fbs_remove=-delta-main_remove
            if fbs_remove>max(Decimal(0),b['fbs']-b['fbs_held']):
                raise HTTPException(409,detail=f"{x['article']} {x['size']}: недостача затрагивает действующий резерв. Перенесите или отмените резерв в задании, затем повторите пересчёт.")
            if fbs_remove and fbs_remove>stock.number(effective_fbs_free_for_sku(db,client_id=c,warehouse_id=w,sku_id=sid)):
                raise HTTPException(409,detail='Товар зарезервирован заказами WB. Сначала перенесите или отмените резерв.')
            main_after=b['main']-main_remove
            if fbs_remove:fbs_ids.add(sid)
        summary.append({**{k:x[k] for k in ('sku_id','barcode','article','size')},
            'before':float(b['physical']),'after':float(b['physical']+delta),'delta':float(delta)})
        if not delta:continue
        line={'sku_id':uuid.UUID(sid),'quantity':abs(delta),'reason':('Излишек' if delta>0 else 'Недостача')+' · ТСД · '+row['picker']}
        direction='destination' if delta>0 else 'source'
        line[direction+'_location_id']=uuid.UUID(place['location_id'])
        line[direction+'_container_id']=uuid.UUID(place['container_id']) if place['container_id'] else None
        ledger.append(StockLine(**line));changes.append((b,b['physical']+delta,main_after))
    op_id=None
    if ledger:
        op=execute_operation(db,OperationCreate(client_id=c,warehouse_id=w,
            operation_type='RECEIVING' if row['mode']=='RECEIVE' else 'ADJUSTMENT',
            idempotency_key='tsd-work-'+row['id'],performed_by=row['picker'],
            reference=(('Приёмка ТСД · '+str(data.get('receipt_number') or '').strip()) if row['mode']=='RECEIVE' and str(data.get('receipt_number') or '').strip() else ('Приёмка ТСД' if row['mode']=='RECEIVE' else 'Инвентаризация ТСД')),
            comment='Сотрудник: '+row['picker']+'; учётная запись: '+owner+(('; номер приёмки: '+str(data.get('receipt_number') or '').strip()) if row['mode']=='RECEIVE' and str(data.get('receipt_number') or '').strip() else ''),
            lines=ledger),commit=False)
        op_id=str(op.id)
        for b,physical,main in changes:
            actual=stock.bucket(db,c,w,b['sku_id'],b['location_id'],b['container_id'])
            if actual['physical']!=physical:
                raise HTTPException(409,detail='Контроль количества не пройден. Изменения отменены.')
            stock.save_mirror(db,c,w,b,physical,main)
        if fbs_ids:queue_fbs(db,uuid.UUID(c),uuid.UUID(w),sorted(fbs_ids))
        # A recount can reduce a stocked box to zero. Such a consumed box is not an
        # active warehouse object anymore; keep the cell/pallet place, archive the box.
        source_boxes=[line.source_container_id for line in ledger if line.source_container_id]
        if source_boxes:
            from app.services.container_lifecycle import archive_consumed_empty_containers
            archive_consumed_empty_containers(
                db,c,w,source_boxes,actor=row['picker'],reason='TSD_STOCKTAKE_EMPTY_BOX',
            )
    data['result']={'operation_id':op_id,'lines':summary,'first_item_at':data.get('first_item_at'),'last_item_at':data.get('last_item_at')}
    data['undo']=[]
    row['status']='POSTED'


def edit(db, wid, c, w, owner, req):
    row=get(db,wid,c,w,owner,lock=True)
    digest=stock.fingerprint(req)
    prior=db.execute(text('SELECT work_id,request_hash FROM fc_tsd_work_events WHERE id=:id'),{'id':req['event_id']}).mappings().first()
    if prior:
        if prior['work_id']!=str(wid) or prior['request_hash']!=digest:
            raise HTTPException(409,detail='Код события уже использован для другой операции')
        result=public(row);result['replayed']=True;return result
    event_at=_effective_event_time(req.get('occurred_at'))
    stored_req={**req,'effective_at':event_at}
    if row['status']!='DRAFT':
        raise HTTPException(409,detail='Черновик уже завершён. Откройте его результат или начните новый.')
    if row['version']!=req['version']:
        raise HTTPException(409,detail='Черновик изменён на другом устройстве. Обновите его перед следующим сканом.')
    data=row['data'];items=data['lines'];action=req['action']
    if action=='SCAN':
        sku=product(db,c,w,req.get('barcode'),req.get('product'))
        sid=str(sku.id)
        if sid not in items:
            if len(items)>=1000:raise HTTPException(422,detail='В черновике уже 1000 позиций')
            items[sid]=line_for(sku, counted=None if row['mode']=='COUNT' else 0)
        x=items[sid];previous=x['quantity']
        if (previous or 0)>=1000000:raise HTTPException(422,detail='Количество слишком большое')
        data['undo']=(data.get('undo',[])+[{'sku_id':sid,'previous':previous,'previous_first_scanned_at':x.get('first_scanned_at'),'previous_last_scanned_at':x.get('last_scanned_at')}])[-100:]
        x['quantity']=(previous or 0)+1
        if row['mode']=='RECEIVE':_touch_received_line(data,x,event_at)
    elif action in ('SET','RESET','REMOVE'):
        sid=req.get('sku_id');x=items.get(sid)
        if not x:raise HTTPException(404,detail='Позиция отсутствует в черновике')
        data['undo']=[v for v in data.get('undo',[]) if v['sku_id']!=sid]
        if action=='SET':
            if req.get('quantity') is None:raise HTTPException(422,detail='Укажите фактическое количество')
            previous_qty=int(x.get('quantity') or 0);x['quantity']=req['quantity']
            if row['mode']=='RECEIVE' and int(req['quantity'] or 0)>previous_qty:_touch_received_line(data,x,event_at)
            elif row['mode']=='RECEIVE':_recalc_receive_times(data)
        elif row['mode']=='COUNT' and x['expected']>0:
            x['quantity']=None
        else:
            del items[sid]
            if row['mode']=='RECEIVE':_recalc_receive_times(data)
    elif action=='CHANGE_PLACE':
        place_req={
            'code':str(req.get('place_code') or '').strip(),
            'target_type':req.get('target_type') or 'AUTO',
            'section_code':str(req.get('section_code') or '').strip(),
            'placement_code':str(req.get('placement_code') or '').strip(),
            'place_empty_box':bool(req.get('place_empty_box')),
            'row':req.get('row'),
        }
        if not place_req['code']:
            raise HTTPException(422,detail='Сканируйте правильный короб или адрес')
        new_place,box,loc=resolve_work_place(db,c,w,place_req,row['picker'],row['mode'])
        old_place=data['place']
        if new_place['location_id']==old_place.get('location_id') and new_place.get('container_id')==old_place.get('container_id'):
            raise HTTPException(409,detail='Это уже текущий короб / место')
        data['place']=new_place
        data['undo']=[]
        if row['mode']=='COUNT':
            baseline,new_lines=_count_lines_for_place(db,c,w,new_place,box,loc)
            data['baseline']=baseline
            data['lines']=new_lines
            items=data['lines']
        data['place_changes']=(data.get('place_changes',[])+[{
            'from':old_place,'to':new_place,'picker':row['picker']
        }])[-30:]
    elif action=='UNDO':
        if not data.get('undo'):raise HTTPException(409,detail='Нет скана для отмены')
        prev=data['undo'].pop();x=items[prev['sku_id']];x['quantity']=prev['previous']
        x['first_scanned_at']=prev.get('previous_first_scanned_at');x['last_scanned_at']=prev.get('previous_last_scanned_at')
        if not x['expected'] and not x['quantity']:del items[prev['sku_id']]
        if row['mode']=='RECEIVE':_recalc_receive_times(data)
    elif action=='CANCEL':
        row['status']='CANCELLED';data['undo']=[]
    elif action=='POST':
        post(db,row,owner)
    else:raise HTTPException(422,detail='Неизвестное действие')
    db.execute(text('''UPDATE fc_tsd_work SET data=CAST(:data AS jsonb),version=version+1,
        status=:status,updated_at=now() WHERE id=:id'''),
        {'id':str(wid),'data':json.dumps(data,ensure_ascii=False),'status':row['status']})
    db.execute(text('''INSERT INTO fc_tsd_work_events(id,work_id,request_hash,action,payload,actor)
        VALUES(:id,:work,:hash,:action,CAST(:payload AS jsonb),:actor)'''),
        {'id':req['event_id'],'work':str(wid),'hash':digest,'action':action,'payload':json.dumps(stored_req,ensure_ascii=False),'actor':owner})
    return public(get(db,wid,c,w,owner))
