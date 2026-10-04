"""Additive placement constraints. No stock changes and no historical backfill."""
from sqlalchemy import text

DDL = [
'''CREATE INDEX IF NOT EXISTS ix_fc25_box_name ON containers(client_id,warehouse_id,lower(name))''',
'''CREATE INDEX IF NOT EXISTS ix_fc25_box_barcode ON containers(client_id,warehouse_id,lower(barcode))''',
'''CREATE INDEX IF NOT EXISTS ix_fc25_bal_occupied ON inventory_balances(warehouse_id,location_id,client_id) WHERE quantity>0''',
'''CREATE TABLE IF NOT EXISTS fc_r145f25_client_audit (
 id uuid PRIMARY KEY, client_id uuid NOT NULL, action text NOT NULL,
 old_name text, new_name text, actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())''',
'''CREATE TABLE IF NOT EXISTS fc_r145f25_place_claims (
 location_id uuid PRIMARY KEY REFERENCES locations(id), warehouse_id uuid NOT NULL REFERENCES warehouses(id),
 client_id uuid NOT NULL REFERENCES clients(id), updated_at timestamptz NOT NULL DEFAULT now())''',
'''CREATE INDEX IF NOT EXISTS ix_fc_r145f25_claim_scope ON fc_r145f25_place_claims(warehouse_id,client_id)''',
# Each statement uses a VOLATILE function, hence sees committed rows after waiting for the claim lock.
'''CREATE OR REPLACE FUNCTION fc_r145f25_claim(c uuid,w uuid,l uuid) RETURNS void LANGUAGE plpgsql VOLATILE AS $$
DECLARE lc locations%ROWTYPE; owner uuid; foreign_owner uuid;
BEGIN
 SELECT * INTO lc FROM locations WHERE id=l FOR UPDATE;
 IF NOT FOUND OR lc.warehouse_id<>w THEN RAISE EXCEPTION 'FC_SCOPE: address belongs to another warehouse' USING ERRCODE='23514'; END IF;
 IF upper(lc.code)='FC-DEFECT-STORAGE' AND lc.location_type='DEFECT' THEN RETURN;END IF;
 IF NOT lc.is_active THEN RAISE EXCEPTION 'FC_SCOPE: address is archived' USING ERRCODE='23514'; END IF;
 IF upper(lc.code) IN (U&'\\0411\\0415\\0417-\\0410\\0414\\0420\\0415\\0421\\0410',U&'\\041F\\0420\\0418\\0415\\041C\\041A\\0410',U&'\\041F\\0420\\0418\\0415\\041C\\041A\\0410-\\041F\\0410\\041B\\0415\\0422\\042B','UNPLACED','FC-DEFECT-STORAGE') THEN RETURN; END IF;
 INSERT INTO fc_r145f25_place_claims(location_id,warehouse_id,client_id) VALUES(l,w,c) ON CONFLICT(location_id) DO NOTHING;
 SELECT client_id INTO owner FROM fc_r145f25_place_claims WHERE location_id=l FOR UPDATE;
 SELECT x.client_id INTO foreign_owner FROM (
   SELECT client_id FROM inventory_balances WHERE warehouse_id=w AND location_id=l AND quantity>0
   UNION ALL SELECT client_id FROM containers WHERE warehouse_id=w AND location_id=l AND status IN ('ACTIVE','DEFECT_ACTIVE')
 ) x WHERE x.client_id<>c LIMIT 1;
 IF foreign_owner IS NOT NULL THEN
   RAISE EXCEPTION 'FC_SCOPE: location occupied by another client' USING ERRCODE='23514', DETAIL=foreign_owner::text;
 END IF;
 IF owner<>c THEN
   UPDATE fc_r145f25_place_claims SET client_id=c,updated_at=now() WHERE location_id=l;
 END IF;
END $$''',
'''CREATE OR REPLACE FUNCTION fc_r145f25_scope_guard() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE j jsonb; oldj jsonb; c uuid; w uuid; l uuid; b uuid; s uuid; incoming boolean; amount numeric; oldamount numeric; box containers%ROWTYPE; loc locations%ROWTYPE; active_client boolean;
BEGIN
 j=to_jsonb(NEW);oldj=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 c=(j->>'client_id')::uuid; w=(j->>'warehouse_id')::uuid;l=NULLIF(j->>'location_id','')::uuid;
 IF TG_TABLE_NAME='containers' THEN
   amount=CASE WHEN j->>'status' IN ('ACTIVE','DEFECT_ACTIVE') THEN 1 ELSE 0 END;
   oldamount=CASE WHEN oldj->>'status' IN ('ACTIVE','DEFECT_ACTIVE') THEN 1 ELSE 0 END;
 ELSE
   amount=COALESCE((j->>'quantity')::numeric,0);oldamount=COALESCE((oldj->>'quantity')::numeric,0);
   IF TG_TABLE_NAME='fc_stocks_v2' THEN
     amount=COALESCE((j->>'storage_only')::numeric,0)+COALESCE((j->>'fbs_available')::numeric,0)+COALESCE((j->>'reserved')::numeric,0);
     oldamount=COALESCE((oldj->>'storage_only')::numeric,0)+COALESCE((oldj->>'fbs_available')::numeric,0)+COALESCE((oldj->>'reserved')::numeric,0);
   END IF;
 END IF;
 IF amount<=0 THEN RETURN NEW;END IF;
 incoming=TG_OP='INSERT' OR amount>oldamount OR (j->>'client_id') IS DISTINCT FROM (oldj->>'client_id') OR (j->>'warehouse_id') IS DISTINCT FROM (oldj->>'warehouse_id') OR (j->>'location_id') IS DISTINCT FROM (oldj->>'location_id') OR (j->>'sku_id') IS DISTINCT FROM (oldj->>'sku_id') OR (j->>'container_id') IS DISTINCT FROM (oldj->>'container_id') OR (j->>'container_key') IS DISTINCT FROM (oldj->>'container_key');
 IF NOT incoming THEN RETURN NEW; END IF;
 SELECT is_active INTO active_client FROM clients WHERE id=c FOR SHARE;
 IF active_client IS DISTINCT FROM true THEN RAISE EXCEPTION 'FC_SCOPE: client missing or archived' USING ERRCODE='23514';END IF;
 PERFORM id FROM warehouses WHERE id=w AND is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'FC_SCOPE: warehouse missing or archived' USING ERRCODE='23514';END IF;
 IF l IS NOT NULL THEN
   SELECT * INTO loc FROM locations WHERE id=l;
   IF NOT FOUND OR loc.warehouse_id<>w THEN RAISE EXCEPTION 'FC_SCOPE: wrong warehouse location' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_TABLE_NAME<>'containers' THEN
   s=NULLIF(j->>'sku_id','')::uuid;
   IF NOT EXISTS(SELECT 1 FROM skus WHERE id=s AND client_id=c) THEN RAISE EXCEPTION 'FC_SCOPE: SKU belongs to another client' USING ERRCODE='23514';END IF;
   b=COALESCE(NULLIF(j->>'container_id',''),NULLIF(j->>'container_key',''))::uuid;
   IF b IS NOT NULL THEN
     SELECT * INTO box FROM containers WHERE id=b;
     IF NOT FOUND OR box.client_id<>c OR box.warehouse_id<>w THEN RAISE EXCEPTION 'FC_SCOPE: box belongs to another client or warehouse' USING ERRCODE='23514';END IF;
   END IF;
 END IF;
 IF l IS NOT NULL AND TG_TABLE_NAME<>'fc_stocks_v2' THEN PERFORM fc_r145f25_claim(c,w,l); END IF;
 RETURN NEW;
END $$''',
'''CREATE OR REPLACE FUNCTION fc_r145f25_final_box_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rownow inventory_balances%ROWTYPE; bx containers%ROWTYPE;
BEGIN
 SELECT * INTO rownow FROM inventory_balances WHERE id=NEW.id;
 IF NOT FOUND OR rownow.quantity<=0 OR rownow.container_id IS NULL THEN RETURN NULL;END IF;
 SELECT * INTO bx FROM containers WHERE id=rownow.container_id;
 IF NOT FOUND OR bx.client_id<>rownow.client_id OR bx.warehouse_id<>rownow.warehouse_id OR bx.location_id IS DISTINCT FROM rownow.location_id THEN
   RAISE EXCEPTION 'FC_SCOPE: final stock/box address mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$''',
'''CREATE OR REPLACE FUNCTION fc_r145f25_container_final() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE bx containers%ROWTYPE;
BEGIN
 SELECT * INTO bx FROM containers WHERE id=NEW.id;
 IF NOT FOUND THEN RETURN NULL;END IF;
 IF EXISTS(SELECT 1 FROM inventory_balances b WHERE b.container_id=bx.id AND b.quantity>0 AND
 (b.client_id<>bx.client_id OR b.warehouse_id<>bx.warehouse_id OR b.location_id IS DISTINCT FROM bx.location_id)) THEN
 RAISE EXCEPTION 'FC_SCOPE: final container address differs from goods' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$''',
'''DROP TRIGGER IF EXISTS fc_r145f25_container_final ON containers''',
'''CREATE CONSTRAINT TRIGGER fc_r145f25_container_final AFTER INSERT OR UPDATE ON containers
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fc_r145f25_container_final()''',
'''DROP TRIGGER IF EXISTS fc_r145f25_balance_final ON inventory_balances''',
'''CREATE CONSTRAINT TRIGGER fc_r145f25_balance_final AFTER INSERT OR UPDATE ON inventory_balances
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fc_r145f25_final_box_scope()''',
]

WORK_TABLES=('fc_box_intakes','fc_r145f19_tsd_receipts','fc_pvz_receiving_sessions','fc_wh_pick_tasks','fc_transit_batches')
WORK_DDL="""CREATE OR REPLACE FUNCTION fc_r145f25_work_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j jsonb;c uuid;w uuid;
BEGIN
 j=to_jsonb(NEW);c=(j->>'client_id')::uuid;w=(j->>'warehouse_id')::uuid;
 IF j->>'status' IN ('COMPLETED','CANCELLED','CLOSED','ARCHIVED','POSTED','SENT','SHIPPED') THEN RETURN NEW; END IF;
 PERFORM id FROM clients WHERE id=c AND is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'FC_SCOPE: client missing or archived' USING ERRCODE='23514';END IF;
 PERFORM id FROM warehouses WHERE id=w AND is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'FC_SCOPE: warehouse missing or archived' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$"""
TABLES=('inventory_balances','containers','fc_stocks_v2')

def install(db):
    db.execute(text("SET LOCAL lock_timeout='5s'"))
    for stmt in DDL:db.execute(text(stmt))
    for table in TABLES:
        if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():
            db.execute(text(f'DROP TRIGGER IF EXISTS fc_r145f25_scope ON {table}'))
            db.execute(text(f'CREATE TRIGGER fc_r145f25_scope BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION fc_r145f25_scope_guard()'))
    db.execute(text(WORK_DDL))
    for table in WORK_TABLES:
        if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():
            db.execute(text(f'DROP TRIGGER IF EXISTS fc_r145f25_work_scope ON {table}'))
            db.execute(text(f'CREATE TRIGGER fc_r145f25_work_scope BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION fc_r145f25_work_scope()'))
    # Caller owns the transaction. No commit here and no inventory UPDATE/DELETE.

def uninstall_guards(db):
    for table in TABLES:
        if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():db.execute(text(f'DROP TRIGGER IF EXISTS fc_r145f25_scope ON {table}'))
    db.execute(text('DROP TRIGGER IF EXISTS fc_r145f25_balance_final ON inventory_balances'))
    db.execute(text('DROP TRIGGER IF EXISTS fc_r145f25_container_final ON containers'))
    for table in WORK_TABLES:
        if db.execute(text('SELECT to_regclass(:t)'),{'t':table}).scalar():db.execute(text(f'DROP TRIGGER IF EXISTS fc_r145f25_work_scope ON {table}'))
    # Preserve additive audit/claim tables. They do not change the old application's reads.


def conflict_counts(db):
    return dict(db.execute(text('''SELECT
 (SELECT count(*) FROM inventory_balances b LEFT JOIN locations l ON l.id=b.location_id LEFT JOIN skus s ON s.id=b.sku_id
   WHERE b.quantity>0 AND (l.warehouse_id IS DISTINCT FROM b.warehouse_id OR s.client_id IS DISTINCT FROM b.client_id)) bad_balance_scope,
 (SELECT count(*) FROM containers c JOIN locations l ON l.id=c.location_id WHERE c.status IN ('ACTIVE','DEFECT_ACTIVE') AND c.warehouse_id<>l.warehouse_id) bad_box_scope,
 (SELECT count(*) FROM (SELECT own.warehouse_id,own.location_id FROM (
    SELECT warehouse_id,location_id,client_id FROM inventory_balances WHERE quantity>0
    UNION ALL SELECT warehouse_id,location_id,client_id FROM containers WHERE status IN ('ACTIVE','DEFECT_ACTIVE') AND location_id IS NOT NULL
   ) own JOIN locations l ON l.id=own.location_id WHERE upper(l.code) NOT IN (U&'\\0411\\0415\\0417-\\0410\\0414\\0420\\0415\\0421\\0410',U&'\\041F\\0420\\0418\\0415\\041C\\041A\\0410',U&'\\041F\\0420\\0418\\0415\\041C\\041A\\0410-\\041F\\0410\\041B\\0415\\0422\\042B','UNPLACED','FC-DEFECT-STORAGE')
   GROUP BY own.warehouse_id,own.location_id HAVING count(DISTINCT client_id)>1) conflicts) mixed_locations''')).mappings().one())
