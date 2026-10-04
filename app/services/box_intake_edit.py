"""Corrections to received boxes, in one scoped transaction with their address and audit."""
import uuid
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import text, select, bindparam
from app.services import box_intake as intake
from app.services.box_routes import set_box_route
from app.services.warehouse_availability import lock_scope


def begin(db,rid,c,w):
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':f'fulfillcore:picking-stock:{c}:{w}'})
    lock_scope(db,c,w)
    r=intake.receipt(db,rid,c,w,True)
    if r['status']=='CANCELLED':raise HTTPException(409,detail='Приёмка удалена. Откройте действующую приёмку')
    return r


def owned_box(db,rid,bid,c,w):
    b=db.execute(text('''SELECT b.*,c.name,c.status,CAST(c.location_id AS varchar) location_id
        FROM fc_box_intake_boxes b JOIN containers c ON CAST(c.id AS varchar)=b.container_id
        WHERE b.receipt_id=:r AND b.container_id=:b AND c.client_id=:c AND c.warehouse_id=:w FOR UPDATE OF b,c'''),
        {'r':str(rid),'b':str(bid),'c':c,'w':w}).mappings().first()
    if not b:raise HTTPException(404,detail='Короб не найден в этой приёмке')
    return dict(b)


def set_available(db,r,pid):
    if pid and r['status']=='OPEN' and r['current_pallet_id']==str(pid):
        p=intake.pallet(db,pid,r['id'])
        if intake.pallet_count(db,p['id'])<p['capacity']:
            db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=false WHERE id=:id'),{'id':p['id']})


def archive_box(db,rid,bid,c,w,by):
    r=begin(db,rid,c,w);b=owned_box(db,rid,bid,c,w)
    intake.empty_box_change(db,bid,c,w,by,archive=True)
    set_available(db,r,b['pallet_id'])
    return {'ok':True,'deleted':[{'id':str(bid)}]}


def assign_route(db,p,bid,c,w,by):
    if p['first_row'] is None:return
    used={int(x[0]):int(x[1]) for x in db.execute(text('''SELECT row_on_pallet,COUNT(*) FROM fc_box_routes
      WHERE location_id=:l AND container_id<>:b GROUP BY row_on_pallet'''),{'l':p['location_id'],'b':str(bid)}).all()}
    side=next((n for n in range(p['first_row'],p['first_row']+(p['capacity']+7)//8) if used.get(n,0)<8),None)
    if side is None:raise HTTPException(409,detail='Нет свободного места в рядах палеты. Добавьте места')
    set_box_route(db,container_id=bid,client_id=c,warehouse_id=w,row_on_pallet=side,actor=by,reason='Перенос короба при приёмке')


def move_contents(db,bid,c,w,destination,by):
    """Use existing ledger primitives and reserve policy; preserve MAIN/FBS classification."""
    from app.models import Container, InventoryBalance, InventoryOperation, InventoryOperationLine
    from app.services.inventory import _container_move_reservation_plan, _lock_balance, _change_balance
    from app.services import stock_actions as actions
    ids=db.execute(select(InventoryBalance.sku_id).where(InventoryBalance.container_id==bid,InventoryBalance.quantity>0)).scalars().all()
    actions.locks(db,c,w,ids)
    box=db.execute(select(Container).where(Container.id==bid).with_for_update()).scalar_one()
    balances=db.execute(select(InventoryBalance).where(InventoryBalance.container_id==bid,InventoryBalance.quantity>0).order_by(InventoryBalance.sku_id).with_for_update()).scalars().all()
    if any(b.client_id!=c or b.warehouse_id!=w or b.location_id!=box.location_id for b in balances):
        raise HTTPException(409,detail='Адрес учётного товара не совпадает с коробом. Требуется сверка')
    if db.execute(text('''SELECT 1 FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
      WHERE l.container_id=:b AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN') LIMIT 1'''),{'b':str(bid)}).first():
        raise HTTPException(409,detail='Короб используется в незакрытой сборке. Завершите или отмените задание')
    plan=_container_move_reservation_plan(db,client_id=c,warehouse_id=w,container=box,balances=balances,lock_rows=True)
    if plan['blockers']:raise HTTPException(409,detail='В коробе есть резерв активной сборки. Завершите или отмените её перед перемещением')
    # Only promises bound to this exact box follow it. Loose promises stay at their address.
    following=[r for r in plan['follow_rows'] if str(r['source_container_id'] or '')==str(bid)]
    old=box.location_id; snapshots=[]
    for b in balances:
        before=actions.bucket(db,c,w,b.sku_id,old,bid)
        after=actions.bucket(db,c,w,b.sku_id,destination,bid)
        follow=sum((Decimal(str(r['quantity'])) for r in following if str(r['sku_id'])==str(b.sku_id)),Decimal(0))
        if before['storage_raw']!=before['main'] or before['main_held'] or before['fbs_held']!=follow or after['physical'] or after['main_held'] or after['fbs_held'] or follow>before['fbs']:
            raise HTTPException(409,detail='Остаток или резерв короба требует сверки. Перемещение не выполнено')
        snapshots.append((b,before,after))
    op=InventoryOperation(client_id=c,warehouse_id=w,operation_type='MOVE_CONTAINER',idempotency_key='intake-move-'+uuid.uuid4().hex,performed_by=by,comment='Перемещение короба из приёмки')
    db.add(op);db.flush()
    for b,before,after in snapshots:
        qty=Decimal(b.quantity);_change_balance(b,-qty)
        dest=_lock_balance(db,c,w,b.sku_id,destination,bid);_change_balance(dest,qty)
        db.add(InventoryOperationLine(operation_id=op.id,sku_id=b.sku_id,quantity=qty,source_location_id=old,source_container_id=bid,destination_location_id=destination,destination_container_id=bid,reason='BOX_INTAKE_MOVE'))
        actions.save_mirror(db,c,w,{**before,'reserved':0},Decimal(0),Decimal(0))
        actions.save_mirror(db,c,w,{**after,'reserved':before['reserved']},qty,before['main'])
    if following:
        db.execute(text('UPDATE fc_stock_reservations SET source_location_id=:loc,updated_at=now() WHERE id IN :ids').bindparams(bindparam('ids',expanding=True)),{'loc':str(destination),'ids':[r['id'] for r in following]})
    box.location_id=destination;db.flush()
    return str(op.id)


def move_box(db,rid,bid,c,w,pid,expand,expected_location,by):
    r=begin(db,rid,c,w);b=owned_box(db,rid,bid,c,w)
    if b['status']!='ACTIVE':raise HTTPException(409,detail='Короб удалён. Сначала восстановите его')
    p=intake.pallet(db,pid,r['id'])
    loc=db.execute(text('SELECT is_active FROM locations WHERE id=:id AND warehouse_id=:w FOR UPDATE'),{'id':p['location_id'],'w':w}).mappings().first()
    if not loc or not loc['is_active']:raise HTTPException(409,detail='Палета удалена')
    if b['location_id']==p['location_id']:return {'ok':True,'already_moved':True}
    if str(expected_location or '')!=str(b['location_id'] or ''):raise HTTPException(409,detail='Короб уже переместили. Обновите приёмку и выберите его заново')
    if db.execute(text("SELECT 1 FROM containers WHERE location_id=:loc AND client_id<>:c AND status='ACTIVE' LIMIT 1"),{'loc':p['location_id'],'c':c}).first() or db.execute(text('SELECT 1 FROM inventory_balances WHERE location_id=:loc AND client_id<>:c AND quantity>0 LIMIT 1'),{'loc':p['location_id'],'c':c}).first():
        raise HTTPException(409,detail='На палете есть товар другого клиента')
    count=intake.pallet_count(db,p['id'])
    if count>=p['capacity']:
        if not expand:raise HTTPException(409,detail='Палета заполнена. Разрешите добавить место сверх лимита')
        if count>=800:raise HTTPException(422,detail='На одной палете не более 800 коробов')
        p['capacity']=count+1
        db.execute(text('UPDATE fc_box_intake_pallets SET capacity=:n WHERE id=:id'),{'id':p['id'],'n':p['capacity']})
    filled=db.execute(text('SELECT 1 FROM inventory_balances WHERE container_id=:b AND quantity<>0 LIMIT 1'),{'b':bid}).first()
    if filled:move_contents(db,bid,c,w,uuid.UUID(p['location_id']),by)
    else:intake.empty_box_change(db,bid,c,w,by,destination=uuid.UUID(p['location_id']))
    db.execute(text('UPDATE fc_box_intake_boxes SET pallet_id=:p WHERE receipt_id=:r AND container_id=:b'),{'p':p['id'],'r':r['id'],'b':str(bid)})
    assign_route(db,p,bid,c,w,by);set_available(db,r,b['pallet_id'])
    if count+1>=p['capacity']:db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=true WHERE id=:id'),{'id':p['id']})
    intake.audit(db,c,w,r['id'],'BOX_PALLET_MOVE',by,before=b,after={'box':b['name'] or b['box_code'],'container_id':str(bid),'pallet_number':p['number'],'location_id':p['location_id']})
    return {'ok':True,'pallet_number':p['number']}


def restore_box(db,rid,bid,c,w,by):
    r=begin(db,rid,c,w);b=owned_box(db,rid,bid,c,w)
    if b['status']=='ACTIVE':return {'ok':True,'already_restored':True}
    if b['status']!='ARCHIVED':raise HTTPException(409,detail='Этот короб нельзя восстановить из приёмки')
    p=intake.pallet(db,b['pallet_id'],r['id']) if b['pallet_id'] else None
    if p and b['location_id']!=p['location_id']:raise HTTPException(409,detail='Короб ранее перемещён из приёмки')
    loc=db.execute(text('SELECT is_active FROM locations WHERE id=:id AND warehouse_id=:w FOR UPDATE'),{'id':b['location_id'],'w':w}).mappings().first()
    if not loc or not loc['is_active']:raise HTTPException(409,detail='Палета удалена. Начните новую приёмку')
    if p and intake.pallet_count(db,p['id'])>=p['capacity']:raise HTTPException(409,detail='Палета заполнена. Добавьте места перед восстановлением')
    if db.execute(text("SELECT 1 FROM containers WHERE location_id=:loc AND client_id<>:c AND status='ACTIVE' LIMIT 1"),{'loc':b['location_id'],'c':c}).first() or db.execute(text('SELECT 1 FROM inventory_balances WHERE location_id=:loc AND client_id<>:c AND quantity>0 LIMIT 1'),{'loc':b['location_id'],'c':c}).first():
        raise HTTPException(409,detail='Адрес уже занят другим клиентом. Короб не восстановлен')
    db.execute(text("UPDATE containers SET status='ACTIVE' WHERE id=:id"),{'id':bid})
    if p:assign_route(db,p,bid,c,w,by)
    intake.audit(db,c,w,r['id'],'BOX_RESTORE',by,before=b,after={'box':b['name'] or b['box_code'],'container_id':str(bid),'pallet_number':p['number'] if p else None})
    return {'ok':True}


def new_pallet(db,rid,pid,c,w,by):
    r=begin(db,rid,c,w)
    old=db.execute(text('SELECT * FROM fc_box_intake_pallets WHERE id=:id'),{'id':str(pid)}).mappings().first()
    if old:
        if old['receipt_id']!=r['id']:raise HTTPException(409,detail='Идентификатор палеты уже используется')
        return {'ok':True,'pallet_id':old['id'],'pallet_number':old['number'],'already_created':True}
    p=intake.create_pallet(db,r,by,pid)
    return {'ok':True,'pallet_id':p['id'],'pallet_number':p['number']}


def select_pallet(db,rid,pid,c,w,by):
    r=begin(db,rid,c,w)
    if r['status']!='OPEN':raise HTTPException(409,detail='Сканирование завершено')
    p=intake.pallet(db,pid,r['id'])
    if not db.execute(text('SELECT 1 FROM locations WHERE id=:id AND is_active=true'),{'id':p['location_id']}).first():raise HTTPException(409,detail='Палета удалена')
    if intake.pallet_count(db,p['id'])>=p['capacity']:raise HTTPException(409,detail='Палета заполнена. Нажмите «Добавить места»')
    db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=:p WHERE id=:r'),{'p':p['id'],'r':r['id']})
    db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=false WHERE id=:p'),{'p':p['id']})
    intake.audit(db,c,w,r['id'],'PALLET_SELECT',by,after={'pallet_number':p['number']})
    return {'ok':True}


def remove_pallet(db,rid,pid,c,w,by):
    r=begin(db,rid,c,w);p=intake.pallet(db,pid,r['id'])
    if db.execute(text('SELECT 1 FROM locations WHERE parent_id=:loc AND is_active=true LIMIT 1'),{'loc':p['location_id']}).first():
        raise HTTPException(409,detail='Внутри палеты есть адреса. Сначала переместите или удалите их')
    # Check all physical boxes, including those moved here from another receipt.
    boxes=db.execute(text("SELECT CAST(id AS varchar) id FROM containers WHERE location_id=:loc AND status='ACTIVE' ORDER BY id FOR UPDATE"),{'loc':p['location_id']}).mappings().all()
    for box in boxes:intake.empty_box_change(db,uuid.UUID(box['id']),c,w,by,archive=True)
    if db.execute(text('SELECT 1 FROM inventory_balances WHERE location_id=:loc AND quantity<>0 LIMIT 1'),{'loc':p['location_id']}).first():raise HTTPException(409,detail='На палете есть товар. Сначала переместите или спишите его')
    if db.execute(text("SELECT 1 FROM fc_stock_reservations WHERE source_location_id=:loc AND status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF','SALE_RESERVED') AND quantity>0 LIMIT 1"),{'loc':p['location_id']}).first():raise HTTPException(409,detail='На палете есть действующий резерв')
    db.execute(text('UPDATE locations SET is_active=false WHERE id=:loc'),{'loc':p['location_id']})
    db.execute(text('UPDATE fc_box_intake_pallets SET is_closed=true WHERE id=:p'),{'p':p['id']})
    db.execute(text('UPDATE fc_box_intakes SET current_pallet_id=NULL WHERE id=:r AND current_pallet_id=:p'),{'r':r['id'],'p':p['id']})
    intake.audit(db,c,w,r['id'],'PALLET_DELETE',by,before=p,after={'pallet_number':p['number'],'boxes_archived':len(boxes)})
    return {'ok':True,'deleted':[{'id':b['id']} for b in boxes]}


def remove_receipt(db,rid,c,w,by):
    r=intake.receipt(db,rid,c,w)
    if r['status']=='CANCELLED':return {'ok':True,'deleted':[]}
    r=begin(db,rid,c,w)
    # Do not delete a receipt whose boxes have moved elsewhere: remove that box from its current location explicitly.
    data=intake.detail(db,rid,c,w);deleted=[]
    for b in data['boxes']:
        if b['status']=='ACTIVE':
            p=next((p for p in data['pallets'] if p['id']==b['pallet_id']),None)
            if p and p['location_id']!=b['location_id']:raise HTTPException(409,detail='В приёмке есть перемещённые короба. Сначала разберите их по одному')
            deleted.extend(archive_box(db,rid,uuid.UUID(b['container_id']),c,w,by)['deleted'])
    for p in data['pallets']:remove_pallet(db,rid,p['id'],c,w,by)
    db.execute(text("UPDATE fc_box_intakes SET status='CANCELLED',closed_at=now(),current_pallet_id=NULL WHERE id=:r"),{'r':r['id']})
    intake.audit(db,c,w,r['id'],'RECEIPT_DELETE',by,before=r,after={'boxes_archived':len(deleted)})
    return {'ok':True,'deleted':deleted}


def remove_sheet(db,sid,c,w,by):
    lock_scope(db,c,w);data=intake.get_box_sheet(db,sid,c,w)
    intake.audit(db,c,w,None,'BOX_SHEET_DELETE',by,before=data,after={'id':str(sid),'number':data['number']})
    db.execute(text('DELETE FROM fc_box_sheets WHERE id=:id AND client_id=:c AND warehouse_id=:w'),{'id':str(sid),**intake.params(c,w)})
    return {'ok':True}
