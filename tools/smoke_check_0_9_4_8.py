#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path
from typing import Any
from fastapi import HTTPException


def fn_source(src: str, name: str) -> str:
    tree=ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name:
            return '\n'.join(src.splitlines()[n.lineno-1:(n.end_lineno or n.lineno)])
    raise AssertionError(f'function missing: {name}')


def need(cond: bool, msg: str) -> None:
    if not cond: raise AssertionError(msg)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root)
    main_s=(root/'app/main.py').read_text(encoding='utf-8')
    mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
    js=(root/'app/static/app.js').read_text(encoding='utf-8')
    cfg=(root/'app/core/config.py').read_text(encoding='utf-8')
    db=(root/'app/core/database.py').read_text(encoding='utf-8')
    pool=(root/'app/services/db_pool_tuning.py').read_text(encoding='utf-8')
    printing=(root/'app/routers/printing.py').read_text(encoding='utf-8')
    runtime=(root/'app/services/runtime_reliability.py').read_text(encoding='utf-8')

    for s in (main_s,mp,cfg,db,pool,printing,runtime): ast.parse(s)
    need('version="0.9.4.8"' in main_s, 'version != 0.9.4.8')
    need('0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT' in mp, 'REV2 marker missing')
    need('0.9.4.8 PLATFORM STABILITY REV2' in main_s, 'platform marker missing')

    consume=fn_source(mp,'_consume_picking_reservation_strict')
    need('require: bool = True' in consume, 'strict consume require=True missing')
    need('PACKED_WITHOUT_WRITE_OFF' in consume, 'write-off diagnostic event missing')
    need('already_consumed' in consume, 'idempotent already_consumed missing')
    need('Заказ не проходил через сборочный лист' in consume, 'no_picking_list operator message missing')

    # Executable unit check for the no-picking-list acceptance rule.
    events=[]
    class Log:
        def error(self,*a,**k): pass
    class Order:
        id='o1'; external_order_num='123'; external_order_id='123'
    env={
        'Session':object,'MarketplaceOrder':object,'Any':Any,'HTTPException':HTTPException,
        'logger':Log(), '_event':lambda *args,**kwargs: events.append((args,kwargs)),
        'consume_order_reservation':lambda *args,**kwargs:{'changed':False,'reason':'no_picking_list'},
    }
    exec(consume,env)
    try:
        env['_consume_picking_reservation_strict'](None,Order(),'TEST')
        raise AssertionError('no_picking_list did not raise 409')
    except HTTPException as exc:
        need(exc.status_code==409, 'no_picking_list wrong HTTP status')
    need(events and events[-1][0][2]=='PACKED_WITHOUT_WRITE_OFF', 'diagnostic event not emitted')
    env['consume_order_reservation']=lambda *args,**kwargs:{'changed':False,'reason':'already_consumed'}
    r=env['_consume_picking_reservation_strict'](None,Order(),'TEST')
    need(r.get('idempotent') is True, 'already_consumed is not idempotent success')

    scan_product=fn_source(mp,'wb_scan_product')
    need('PACKING_WITHOUT_PICKING_LIST' not in scan_product, 'old permissive no-picking-list path remains')
    need('PACKING_RESERVATION_REQUIRED' in scan_product, 'reservation precheck missing')
    need(scan_product.find('if not matched.last_picking_list_id') < scan_product.find('matched.product_scanned_barcode'), 'reservation block occurs too late')

    scan_chz=fn_source(mp,'wb_scan_chz')
    need('order.local_status = "PICKING"' in scan_chz and 'packed": False' in scan_chz, 'scan-chz must not mark PACKED')
    finalize=fn_source(mp,'_finalize_strict_pack_from_job')
    need(finalize.find('if not outcome.get("success")') < finalize.find('_consume_picking_reservation_strict'), 'write-off can happen before print ACK')
    need(finalize.find('_consume_picking_reservation_strict') < finalize.find('order.local_status = "PACKED"'), 'PACKED can happen before write-off')
    need('release_packing_lease' in finalize, 'packing lease release missing after done')

    worker=fn_source(mp,'_schedule_strict_pack_gate')
    need(worker.find('job = queue_print_job') < worker.find('old_job.status = "CANCELLED"'), 'strict queue cancels old job before replacement exists')
    need('supplier == "new"' in worker and 'supplier in {"new", "complete"}' not in worker, 'complete order still forced into supply')
    need('stage="CHZ_VERIFIED"' in worker and 'stage="WAIT_LABEL"' in worker, 'WB verification/label stages missing')
    need('FC_PACK_GATE_PROGRAM_ERROR' in worker, 'programmer errors still silently retried')

    generic=fn_source(mp,'_schedule_wb_auto_label_background')
    need(generic.find('job = queue_print_job') < generic.find('old_job.status = "CANCELLED"'), 'generic auto-label cancels before replacement')
    need('_fc_auto_wb_label_pending' in generic and 'queued_ok' in generic and 'finally:' in generic, 'pending marker cleanup missing')
    route=fn_source(mp,'wb_queue_order_label')
    need('Отменено 0.9.4.7' not in route, '0.9.4.7 premature cancel remains in route')

    apply_meta=fn_source(mp,'_apply_wb_order_meta')
    need('strict_gate_active' in apply_meta and 'if order.chz_code and (strict_gate_active or not actual)' in apply_meta, 'passive WB sync can still detach scanner CHZ')
    need('def wb_packed_without_writeoff' in mp, 'packed-without-writeoff diagnostic endpoint missing')
    need('_CHZ_BG_SLOTS = threading.BoundedSemaphore(8)' in mp, 'CHZ background concurrency not raised to 8')
    need('stale_keys = [k for k, until in _CHZ_BG_NEXT_AT.items()' in mp, 'CHZ cooldown dictionary cleanup missing')

    need('wbBackgroundPrintChains: {}' in js, 'legacy label queue still global')
    need('const chains=state.wbBackgroundPrintChains' in js, 'per-station legacy chain missing')
    need('await waitWbStrictPackingGate' in js, 'strict UI gate missing')
    need('if(p.done&&p.printed&&p.packed)' in js, 'next scan is not gated on printed+packed')
    need('setTimeout(resolve,250)' in js, 'fast progress polling missing')
    need('PRINT AGENT 1.1.7 SAFE SIMPLE' in js, 'website still advertises a newer/unapproved Print Agent')
    need('FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip' in main_s, 'site download does not point to Print Agent 1.1.7 SAFE SIMPLE')

    need('db_pool_size: int = Field(default=20' in cfg, 'Settings db_pool_size missing')
    need('FC_DB_POOL_TIMEOUT' in cfg, 'Settings pool env alias missing')
    need('pool_size": settings.db_pool_size' in db and 'pool_timeout": settings.db_pool_timeout' in db, 'create_engine pool configuration missing')
    for bad in ('._max_overflow','._timeout','._recycle','._pre_ping','._pool'):
        need(bad not in pool, f'private QueuePool mutation remains: {bad}')
    need('configured_in_create_engine' in pool, 'read-only pool status shim missing')

    auth=fn_source(main_s,'single_profile_auth')
    need(auth.find('generator.close()') < auth.find('response = await call_next(request)'), 'commercial auth DB session crosses call_next')
    need('rebuilt = Response(' in auth, 'response body rebuild fix missing')
    startup=fn_source(main_s,'reliability_startup')
    need('run_db_step("db_pool"' in startup and 'run_db_step("runtime_schema"' in startup, 'startup steps not isolated')

    nxt=fn_source(printing,'next_print_job')
    need('select(PrintJob.id)' in nxt and '.limit(1)' in nxt, 'print poll is not id-only LIMIT 1')
    need('db.commit()  # REV2 reclaim commit' in nxt, 'stale CLAIMED reclaim not committed separately')
    pres=fn_source(printing,'print_result')
    need('REV2 station ownership' in pres and 'auth_station' in pres, 'print result station ownership missing')

    lock=fn_source(runtime,'distributed_lock')
    need('pg_try_advisory_lock' in lock and 'timeout_seconds: float = 5.0' in lock, 'distributed lock is still unbounded')
    need('pg_advisory_lock(' not in lock, 'blocking pg_advisory_lock remains')

    # All marketplace queue calls through the payload field are normalized.
    need('station_code=payload.print_station' not in mp, 'unnormalized payload print station remains')

    print('OK: FulfillCore 0.9.4.8 REV2 acceptance checks passed')

if __name__=='__main__': main()
