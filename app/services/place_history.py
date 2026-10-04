"""Read-only storage history. One operation produces one net change per place.

Physical box history follows its container id across addresses and products.
Location history includes every box and loose unit at that address. Product
history is explicitly about the selected product, never about the whole box.
No balances, reservations, schema, or existing audit rows are written here.
"""
from decimal import Decimal

from sqlalchemy import bindparam, text


# FC_K9R63_PLACE_HISTORY: all balances and movements share one SELECT snapshot.
def place_history(db, *, client_id, warehouse_id, sku_ids=None, location_id=None, container_id=None):
    product_mode = location_id is None and container_id is None
    if product_mode and not sku_ids:
        return []
    params = {'client': str(client_id), 'warehouse': str(warehouse_id)}
    if container_id is not None:
        if location_id is not None:
            raise ValueError('Choose a box or a location, not both')
        # Separate UUID-column and text-CTE binds. PostgreSQL infers one type
        # per parameter; reusing :box here caused `text = uuid` (42883).
        params['box'] = str(container_id)
        params['box_native'] = str(container_id)
        boxes = ''
        line_filter = '(l.source_container_id=:box_native OR l.destination_container_id=:box_native)'
        balance_filter = 'b.container_id=:box_native'
        event_scopes = """SELECT 'BOX' AS scope, NULL AS place_location_id,
            container_id,location_id,operation_id,created_at,delta
            FROM ends WHERE container_id=:box"""
        current_scopes = """SELECT 'BOX' AS scope,NULL AS place_location_id,
            container_id,quantity FROM balances WHERE container_id=:box"""
        final_filter = ''
    elif product_mode:
        params['ids'] = list(sku_ids)
        boxes = ''
        line_filter = 'l.sku_id IN :ids'
        balance_filter = 'b.sku_id IN :ids'
        event_scopes = """
          SELECT 'PRODUCT' AS scope, location_id AS place_location_id,
                 container_id, location_id, operation_id, created_at, delta
          FROM ends
        """
        current_scopes = """
          SELECT 'PRODUCT' AS scope, location_id AS place_location_id,
                 container_id, quantity FROM balances
        """
        final_filter = ''
    else:
        params['location'] = str(location_id)
        params['location_native'] = str(location_id)
        boxes = """
          boxes AS (
            SELECT l.source_container_id AS id
            FROM inventory_operation_lines l JOIN inventory_operations o ON o.id=l.operation_id
            WHERE o.client_id=:client AND o.warehouse_id=:warehouse
              AND l.source_location_id=:location_native AND l.source_container_id IS NOT NULL
            UNION
            SELECT l.destination_container_id AS id
            FROM inventory_operation_lines l JOIN inventory_operations o ON o.id=l.operation_id
            WHERE o.client_id=:client AND o.warehouse_id=:warehouse
              AND l.destination_location_id=:location_native AND l.destination_container_id IS NOT NULL
          ),
        """
        line_filter = '''(l.source_location_id=:location_native OR l.destination_location_id=:location_native
                         OR l.source_container_id IN (SELECT id FROM boxes)
                         OR l.destination_container_id IN (SELECT id FROM boxes))'''
        balance_filter = '(b.location_id=:location_native OR b.container_id IN (SELECT id FROM boxes))'
        event_scopes = """
          SELECT 'LOCATION' AS scope, location_id AS place_location_id,
                 NULL AS container_id, location_id, operation_id, created_at, delta
          FROM ends WHERE location_id=:location
          UNION ALL
          SELECT 'BOX' AS scope, NULL AS place_location_id,
                 container_id, location_id, operation_id, created_at, delta
          FROM ends WHERE container_id IS NOT NULL
        """
        current_scopes = """
          SELECT 'LOCATION' AS scope, location_id AS place_location_id,
                 NULL AS container_id, quantity FROM balances WHERE location_id=:location
          UNION ALL
          SELECT 'BOX' AS scope, NULL AS place_location_id, container_id, quantity
          FROM balances WHERE container_id IS NOT NULL
        """
        final_filter = 'AND n.location_id=:location'

    sql = f"""
      WITH {boxes}
      movements AS (
        SELECT o.id AS operation_id, o.created_at,
               CAST(l.source_location_id AS TEXT) AS source_location_id,
               CAST(l.source_container_id AS TEXT) AS source_container_id,
               CAST(l.destination_location_id AS TEXT) AS destination_location_id,
               CAST(l.destination_container_id AS TEXT) AS destination_container_id, l.quantity
        FROM inventory_operations o JOIN inventory_operation_lines l ON l.operation_id=o.id
        WHERE o.client_id=:client AND o.warehouse_id=:warehouse
          AND (UPPER(COALESCE(o.status,'COMPLETED'))<>'CANCELLED' OR EXISTS (
            SELECT 1 FROM inventory_operations reversal
            WHERE reversal.client_id=o.client_id AND reversal.warehouse_id=o.warehouse_id
              AND reversal.idempotency_key='cancel-receiving-' || CAST(o.id AS TEXT)
              AND UPPER(COALESCE(reversal.status,'COMPLETED'))<>'CANCELLED'
          )) AND {line_filter}
      ), ends AS (
        SELECT operation_id,created_at,source_location_id AS location_id,
               source_container_id AS container_id,-quantity AS delta
        FROM movements WHERE source_location_id IS NOT NULL
        UNION ALL
        SELECT operation_id,created_at,destination_location_id AS location_id,
               destination_container_id AS container_id,quantity AS delta
        FROM movements WHERE destination_location_id IS NOT NULL
      ), event_scopes AS ({event_scopes}), ev AS (
        SELECT scope,place_location_id,container_id,operation_id,created_at,
               MAX(location_id) AS location_id,SUM(delta) AS delta
        FROM event_scopes
        GROUP BY scope,place_location_id,container_id,operation_id,created_at
        HAVING SUM(delta)<>0
      ), balances AS (
        SELECT CAST(b.location_id AS TEXT) AS location_id,
               CAST(b.container_id AS TEXT) AS container_id,b.quantity
        FROM inventory_balances b
        WHERE b.client_id=:client AND b.warehouse_id=:warehouse AND {balance_filter}
      ), current_scopes AS ({current_scopes}), cur AS (
        SELECT scope,place_location_id,container_id,SUM(quantity) AS current_qty
        FROM current_scopes GROUP BY scope,place_location_id,container_id
      ), calc AS (
        SELECT e.*, COALESCE(c.current_qty,0)
          - SUM(e.delta) OVER (PARTITION BY e.scope,e.place_location_id,e.container_id)
          + COALESCE(SUM(e.delta) OVER (
              PARTITION BY e.scope,e.place_location_id,e.container_id
              ORDER BY e.created_at,e.operation_id
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ),0) AS before_qty
        FROM ev e LEFT JOIN cur c ON c.scope=e.scope
          AND COALESCE(c.place_location_id,'')=COALESCE(e.place_location_id,'')
          AND COALESCE(c.container_id,'')=COALESCE(e.container_id,'')
      ), steps AS (
        SELECT calc.*,before_qty+delta AS after_qty FROM calc
      ), numbered AS (
        SELECT steps.*,SUM(CASE WHEN before_qty>0 AND after_qty=0 THEN 1 ELSE 0 END)
          OVER (PARTITION BY scope,place_location_id,container_id
                ORDER BY created_at,operation_id ROWS UNBOUNDED PRECEDING) AS empty_count
        FROM steps
      )
      SELECT n.*,o.performed_by,o.reference,o.comment,o.operation_type,o.status AS operation_status,
             loc.code AS location_code,COALESCE(cont.name,cont.barcode) AS container_name
      FROM numbered n JOIN inventory_operations o ON o.id=n.operation_id
      LEFT JOIN locations loc ON CAST(loc.id AS TEXT)=n.location_id
      LEFT JOIN containers cont ON CAST(cont.id AS TEXT)=n.container_id
      WHERE ((n.before_qty>0 AND n.after_qty=0) OR (n.before_qty=0 AND n.after_qty>0))
        {final_filter}
      ORDER BY n.created_at DESC,n.operation_id DESC,n.scope,n.container_id
    """
    stmt = text(sql)
    if product_mode:
        stmt = stmt.bindparams(bindparam('ids', expanding=True))
    return [_event(row) for row in db.execute(stmt, params).mappings().all()]


def _event(row):
    empty = Decimal(str(row['after_qty'])) == 0
    refilled = not empty and int(row['empty_count'] or 0) > 0
    event_type = 'PLACE_EMPTY' if empty else 'PLACE_REFILLED' if refilled else 'PLACE_FIRST_STOCK'
    scope = row['scope']
    if scope == 'BOX':
        label = 'Короб опустел' if empty else 'Короб заполнен снова' if refilled else 'Товар добавлен в пустой короб'
    elif scope == 'LOCATION':
        label = 'Ячейка опустела' if empty else 'Ячейка заполнена снова' if refilled else 'Товар добавлен в пустую ячейку'
    else:
        label = 'Товар закончился' if empty else 'Товар снова добавлен' if refilled else 'Товар добавлен'
    place = {'location': row['location_code'], 'container': row['container_name']}
    return {
        'operation_id': str(row['operation_id']), 'type': event_type, 'scope': scope,
        'status': 'COMPLETED', 'label': label, 'comment': label, 'reason': row['comment'],
        'created_at': row['created_at'], 'performed_by': row['performed_by'],
        'reference': row['reference'], 'operation_type': row['operation_type'],
        'operation_status': row['operation_status'],
        'location_id': str(row['location_id']),
        'container_id': str(row['container_id']) if row['container_id'] else None,
        'container': row['container_name'],
        'before_qty': float(row['before_qty']), 'after_qty': float(row['after_qty']),
        'quantity': abs(float(row['delta'])), 'place_lifecycle': True,
        'source': place if empty else None, 'destination': None if empty else place,
    }


def product_place_summary(events):
    summaries = {}
    for event in reversed(events):
        key = (event['location_id'], event['container_id'] or '')
        info = summaries.setdefault(key, {
            'first_stock_at': None, 'last_empty_at': None, 'last_refilled_at': None,
            'refilled_after_empty_at': None, 'refill_count': 0, 'empty_count': 0,
        })
        if event['type'] == 'PLACE_EMPTY':
            info['last_empty_at'] = event['created_at']
            info['empty_count'] += 1
        elif event['type'] == 'PLACE_REFILLED':
            info['last_refilled_at'] = event['created_at']
            info['refilled_after_empty_at'] = info['last_empty_at']
            info['refill_count'] += 1
        elif not info['first_stock_at']:
            info['first_stock_at'] = event['created_at']
    return summaries
