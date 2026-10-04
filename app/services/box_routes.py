"""Additive box-side metadata and immutable picking-route snapshots.

No stock, reservation or WB publishing quantities are changed by this module.
Schema is installed only by the release installer, after its verified backup.
"""
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import text
from app.services.picking_route import location_route, route_key, route_label, choose_position

DDL = [
    '''CREATE TABLE IF NOT EXISTS fc_box_routes (
       container_id VARCHAR(36) PRIMARY KEY, client_id VARCHAR(36) NOT NULL,
       warehouse_id VARCHAR(36) NOT NULL, location_id VARCHAR(36) NOT NULL,
       row_on_pallet INTEGER NOT NULL CHECK (row_on_pallet IN (1,2)),
       row_position INTEGER NOT NULL CHECK (row_position BETWEEN 1 AND 8),
       actor VARCHAR(120), updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
       UNIQUE(location_id,row_on_pallet,row_position))''',
    'CREATE INDEX IF NOT EXISTS ix_fc_box_routes_scope ON fc_box_routes(client_id,warehouse_id)',
    '''CREATE TABLE IF NOT EXISTS fc_box_route_audit (
       id VARCHAR(36) PRIMARY KEY, container_id VARCHAR(36) NOT NULL,
       client_id VARCHAR(36) NOT NULL, warehouse_id VARCHAR(36) NOT NULL,
       before_json JSONB, after_json JSONB, actor VARCHAR(120), reason TEXT NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    'CREATE INDEX IF NOT EXISTS ix_fc_box_route_audit_box ON fc_box_route_audit(container_id,created_at)',
    '''CREATE TABLE IF NOT EXISTS fc_pick_route_snapshots (
       kind VARCHAR(12) NOT NULL, task_id VARCHAR(36) NOT NULL, line_id VARCHAR(36) NOT NULL,
       route_json JSONB NOT NULL, sort_order INTEGER NOT NULL,
       created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
       PRIMARY KEY(kind,task_id,line_id))''',
    # A move away and back must never resurrect a stale side assignment. The
    # trigger only affects the new metadata for the changed box, not its stock.
    '''CREATE OR REPLACE FUNCTION fc_r66_clear_moved_box_route() RETURNS trigger
       LANGUAGE plpgsql AS $$
       DECLARE prior fc_box_routes%ROWTYPE;
       BEGIN
         IF OLD.location_id IS DISTINCT FROM NEW.location_id
            OR (OLD.status IS DISTINCT FROM NEW.status AND NEW.status <> 'ACTIVE') THEN
           DELETE FROM fc_box_routes WHERE container_id=CAST(NEW.id AS varchar) RETURNING * INTO prior;
           IF FOUND THEN
             INSERT INTO fc_box_route_audit(id,container_id,client_id,warehouse_id,before_json,after_json,actor,reason)
             VALUES(md5(random()::text || clock_timestamp()::text),prior.container_id,
               prior.client_id,prior.warehouse_id,to_jsonb(prior),NULL,'Система',
               'Ряд сброшен после перемещения или архивации короба; новое расположение требует выбора ряда');
           END IF;
         END IF;
         RETURN NEW;
       END $$''',
    'DROP TRIGGER IF EXISTS fc_r66_box_route_move ON containers',
    '''CREATE TRIGGER fc_r66_box_route_move AFTER UPDATE OF location_id,status ON containers
       FOR EACH ROW EXECUTE FUNCTION fc_r66_clear_moved_box_route()''',
]


def install_schema(db):
    # Transactional DDL, called explicitly by the installer, never from a hot read.
    db.execute(text("SET LOCAL lock_timeout='5s'"))
    for sql in DDL: db.execute(text(sql))


def ready(db):
    info = getattr(db, 'info', {})
    if 'fc_r66_routes_ready' not in info:
        info['fc_r66_routes_ready'] = bool(db.execute(text("SELECT to_regclass('fc_box_routes')")).scalar())
    return info['fc_r66_routes_ready']


def load_context(db, client_id, warehouse_id):
    p = {'c':str(client_id),'w':str(warehouse_id)}
    locs = {str(x['id']):dict(x) for x in db.execute(text('''
        SELECT CAST(id AS varchar) id, CAST(parent_id AS varchar) parent_id,code,location_type
        FROM locations WHERE CAST(warehouse_id AS varchar)=:w'''), p).mappings().all()}
    routes = {}
    if ready(db):
        routes = {str(x['container_id']):dict(x) for x in db.execute(text('''
            SELECT r.* FROM fc_box_routes r JOIN containers c ON CAST(c.id AS varchar)=r.container_id
            WHERE r.client_id=:c AND r.warehouse_id=:w AND c.status='ACTIVE'
              AND CAST(c.location_id AS varchar)=r.location_id
              AND CAST(c.client_id AS varchar)=r.client_id AND CAST(c.warehouse_id AS varchar)=r.warehouse_id
            '''),p).mappings().all()}
    return locs, routes


def enrich(db, client_id, warehouse_id, rows):
    locs, routes = load_context(db, client_id, warehouse_id)
    out = []
    for value in rows:
        row = dict(value); lid = str(row.get('location_id') or '')
        route = location_route(lid, locs)
        own = routes.get(str(row.get('container_id') or ''))
        if own and own['location_id'] == lid:
            route.update(route_row=int(own['row_on_pallet']), route_position=int(own['row_position']))
        row.update(route); row['route_label'] = route_label(row); out.append(row)
    return out


def audit(db, *, container_id, client_id, warehouse_id, before, after, actor, reason):
    db.execute(text('''INSERT INTO fc_box_route_audit(id,container_id,client_id,warehouse_id,
        before_json,after_json,actor,reason) VALUES(:id,:box,:c,:w,CAST(:before AS jsonb),CAST(:after AS jsonb),:actor,:reason)'''),
        {'id':str(uuid.uuid4()),'box':str(container_id),'c':str(client_id),'w':str(warehouse_id),
         'before':json.dumps(before,default=str,ensure_ascii=False) if before else None,
         'after':json.dumps(after,default=str,ensure_ascii=False) if after else None,
         'actor':str(actor or 'Администратор')[:120], 'reason':reason})


def set_box_route(db, *, container_id, client_id, warehouse_id, row_on_pallet, actor,
                  reason='Выбор ряда при приёмке'):
    if not ready(db): raise HTTPException(503,detail='Сначала завершите установку r66: схема рядов не установлена')
    # Callers acquire the MAIN scope lock before locking the box. This prevents a
    # route edit from racing creation of a new pick, without touching its claims.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
               {'key':f'fc:r66:box-routes:{warehouse_id}'})
    db.flush()
    box = db.execute(text('''SELECT CAST(id AS varchar) id,CAST(location_id AS varchar) location_id,status
        FROM containers WHERE CAST(id AS varchar)=:box AND CAST(client_id AS varchar)=:c
          AND CAST(warehouse_id AS varchar)=:w FOR UPDATE'''),
        {'box':str(container_id),'c':str(client_id),'w':str(warehouse_id)}).mappings().first()
    if not box or box['status']!='ACTIVE': raise HTTPException(404,detail='Действующий короб не найден')
    old = db.execute(text('SELECT * FROM fc_box_routes WHERE container_id=:box'),
                     {'box':str(container_id)}).mappings().first()
    before = dict(old) if old else None
    if old and old['location_id']==box['location_id'] and old['row_on_pallet']==row_on_pallet:
        return dict(old)
    if not old and row_on_pallet is None: return None
    if db.execute(text('''SELECT 1 FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
        WHERE l.container_id=:box AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN') LIMIT 1'''),
        {'box':str(container_id)}).first():
        raise HTTPException(409,detail='Короб используется в незакрытой сборке. Завершите или отмените её перед изменением ряда')
    after = None
    if row_on_pallet is not None:
        if type(row_on_pallet) is not int or not 1 <= row_on_pallet <= 999:
            raise HTTPException(422,detail='Номер ряда должен быть от 1 до 999; в каждом ряду до 8 коробов')
        locs, _ = load_context(db, client_id, warehouse_id)
        route = location_route(box['location_id'], locs)
        if not route['route_pallet_id'] or route['route_pallet_id']!=box['location_id']:
            raise HTTPException(422,detail='Для ряда выберите палету или паллетоместо. Обычная ячейка не является стороной палеты')
        occupied = {int(r[0]) for r in db.execute(text('''SELECT row_position FROM fc_box_routes
            WHERE location_id=:loc AND row_on_pallet=:side AND container_id<>:box'''),
            {'loc':box['location_id'],'side':row_on_pallet,'box':str(container_id)}).all()}
        try: pos = choose_position(occupied)
        except ValueError as e: raise HTTPException(409,detail=str(e))
        after = {'container_id':str(container_id),'client_id':str(client_id),'warehouse_id':str(warehouse_id),
                 'location_id':box['location_id'],'row_on_pallet':row_on_pallet,'row_position':pos}
        db.execute(text('''INSERT INTO fc_box_routes(container_id,client_id,warehouse_id,location_id,row_on_pallet,row_position,actor)
            VALUES(:container_id,:client_id,:warehouse_id,:location_id,:row_on_pallet,:row_position,:actor)
            ON CONFLICT(container_id) DO UPDATE SET location_id=EXCLUDED.location_id,row_on_pallet=EXCLUDED.row_on_pallet,
              row_position=EXCLUDED.row_position,actor=EXCLUDED.actor,updated_at=CURRENT_TIMESTAMP'''),
            {**after,'actor':str(actor or 'Администратор')[:120]})
    else:
        db.execute(text('DELETE FROM fc_box_routes WHERE container_id=:box'),{'box':str(container_id)})
    audit(db,container_id=container_id,client_id=client_id,warehouse_id=warehouse_id,
          before=before,after=after,actor=actor,reason=reason)
    return after


def snapshots(db, kind, task_id, rows):
    if not ready(db): return [dict(r) for r in rows]
    stored = {str(r['line_id']):r for r in db.execute(text('''SELECT line_id,route_json,sort_order
        FROM fc_pick_route_snapshots WHERE kind=:kind AND task_id=:task'''),
        {'kind':kind,'task':str(task_id)}).mappings().all()}
    out = []
    for x in rows:
        row = dict(x); saved = stored.get(str(row['id']))
        if saved:
            route = saved['route_json']
            if isinstance(route,str): route=json.loads(route)
            row.update(route); row['_route_order']=int(saved['sort_order'])
        out.append(row)
    if stored: out.sort(key=lambda r:(r.get('_route_order',10**9),r.get('sort_order',0),str(r['id'])))
    return out


def freeze_rows(db, kind, task_id, client_id, warehouse_id, rows):
    """Append-only snapshots; repeat export does not pick up a changed box side."""
    if not ready(db): raise HTTPException(503,detail='Схема маршрутов r66 не установлена')
    enriched = sorted(enrich(db,client_id,warehouse_id,rows),key=route_key)
    for ix, row in enumerate(enriched):
        route = {k:v for k,v in row.items() if k.startswith('route_')}
        db.execute(text('''INSERT INTO fc_pick_route_snapshots(kind,task_id,line_id,route_json,sort_order)
            VALUES(:kind,:task,:line,CAST(:route AS jsonb),:sort) ON CONFLICT(kind,task_id,line_id) DO NOTHING'''),
            {'kind':kind,'task':str(task_id),'line':str(row['id']),'route':json.dumps(route,ensure_ascii=False),'sort':ix})
    return snapshots(db,kind,task_id,rows)


def freeze_task(db, task_id):
    task = db.execute(text('SELECT client_id,warehouse_id FROM fc_wh_pick_tasks WHERE id=:id'),
                      {'id':str(task_id)}).mappings().one()
    rows = db.execute(text('''SELECT l.*,s.article,s.size,s.barcode,loc.code location_code,
        c.name container_name,c.barcode container_barcode FROM fc_wh_pick_lines l
        LEFT JOIN skus s ON CAST(s.id AS varchar)=l.sku_id
        LEFT JOIN locations loc ON CAST(loc.id AS varchar)=l.location_id
        LEFT JOIN containers c ON CAST(c.id AS varchar)=l.container_id WHERE l.task_id=:id'''),
        {'id':str(task_id)}).mappings().all()
    return freeze_rows(db,'MAIN',task_id,task['client_id'],task['warehouse_id'],rows)
