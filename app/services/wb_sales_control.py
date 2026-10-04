"""Persistent sale policy, independent of free stock and order cancellation."""
from sqlalchemy import text, bindparam

def install_schema(db):
    db.execute(text("""CREATE TABLE IF NOT EXISTS fc_wb_scope_pauses (
        client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL,
        paused boolean NOT NULL DEFAULT FALSE, actor text, updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(client_id,warehouse_id))"""))

    db.execute(text("""CREATE TABLE IF NOT EXISTS fc_wb_sale_exceptions (
        client_id varchar(36) NOT NULL,warehouse_id varchar(36) NOT NULL,sku_id varchar(36) NOT NULL,
        actor text,updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(client_id,warehouse_id,sku_id))"""))

def global_paused(db,c,w):
    # Compatibility with the read-only preflight before the additive migration.
    # Database errors propagate: inability to read a stop must never permit publication.
    if not db.execute(text("SELECT to_regclass('fc_wb_scope_pauses')")).scalar():
        return False
    return bool(db.execute(text("SELECT paused FROM fc_wb_scope_pauses WHERE client_id=:c AND warehouse_id=:w"),
        {'c':str(c),'w':str(w)}).scalar())

def set_global(db,c,w,paused,actor):
    # A new stop-all resets every prior explicit exception, including absent stock.
    db.execute(text('DELETE FROM fc_wb_sale_exceptions WHERE client_id=:c AND warehouse_id=:w'),{'c':str(c),'w':str(w)})
    db.execute(text("""INSERT INTO fc_wb_scope_pauses(client_id,warehouse_id,paused,actor)
        VALUES(:c,:w,:p,:a) ON CONFLICT(client_id,warehouse_id) DO UPDATE
        SET paused=EXCLUDED.paused,actor=EXCLUDED.actor,updated_at=now()"""),
        {'c':str(c),'w':str(w),'p':bool(paused),'a':str(actor or 'admin')[:120]})

def paused_ids(db,c,w,ids):
    ids=[str(x) for x in ids]
    if not ids:return set()
    if global_paused(db,c,w):
        allowed=set()
        if db.execute(text("SELECT to_regclass('fc_wb_sale_exceptions')")).scalar():
            allowed=set(db.execute(text('SELECT sku_id FROM fc_wb_sale_exceptions WHERE client_id=:c AND warehouse_id=:w AND sku_id IN :ids')
                .bindparams(bindparam('ids',expanding=True)),{'c':str(c),'w':str(w),'ids':ids}).scalars())
        stopped=set(ids)-allowed
    else:stopped=set()
    return stopped | set(str(x) for x in db.execute(text("""SELECT CAST(sku_id AS varchar)
        FROM fc_wb_sales_pauses WHERE CAST(client_id AS varchar)=:c
        AND CAST(warehouse_id AS varchar)=:w AND paused=TRUE AND CAST(sku_id AS varchar) IN :ids""")
        .bindparams(bindparam('ids',expanding=True)),{'c':str(c),'w':str(w),'ids':ids}).scalars())


def resume_selected(db,c,w,ids,actor):
    if not global_paused(db,c,w):return
    for sid in sorted(set(str(s) for s in ids)):
        db.execute(text("""INSERT INTO fc_wb_sale_exceptions(client_id,warehouse_id,sku_id,actor)
            VALUES(:c,:w,:s,:a) ON CONFLICT(client_id,warehouse_id,sku_id) DO UPDATE SET actor=EXCLUDED.actor,updated_at=now()"""),
            {'c':str(c),'w':str(w),'s':sid,'a':actor})

def remove_exception(db,c,w,s):
    # The old runtime can execute the candidate during a read-only preflight.
    if db.execute(text("SELECT to_regclass('fc_wb_sale_exceptions')")).scalar():
        db.execute(text('DELETE FROM fc_wb_sale_exceptions WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s'),{'c':str(c),'w':str(w),'s':str(s)})
