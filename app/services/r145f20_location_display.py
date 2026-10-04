"""Human warehouse address formatting. Storage identifiers stay unchanged."""
from __future__ import annotations
import re
from sqlalchemy import select,text,bindparam
from app.models import Location, Container

_TECHNICAL = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f-]{27,}|(?:FCLOC|LOC|LOCATION)[_-][0-9a-f-]{12,})$", re.I)

def location_label(location, by_id=None):
    from app.services.r145f25_places import address_label
    return address_label(location,by_id)


def load_location_map(db, warehouse_id, location_ids):
    """Load only referenced addresses and their parents; never the whole warehouse per scan."""
    pending = {str(x) for x in location_ids if x}
    by_id = {}
    for _ in range(20):
        pending -= set(by_id)
        if not pending:
            break
        rows = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id, Location.id.in_(sorted(pending))
        )).scalars().all()
        pending = set()
        for row in rows:
            by_id[str(row.id)] = row
            parent = getattr(row, "parent_id", None)
            if parent and str(parent) not in by_id:
                pending.add(str(parent))
    return by_id

def enrich_receipt(db, receipt, warehouse_id):
    if not receipt:
        return receipt
    by_id = load_location_map(db, warehouse_id, [p.get("location_id") for p in receipt.get("places", [])])
    ids = [p.get("container_id") for p in receipt.get("places", []) if p.get("container_id")]
    boxes = db.execute(select(Container).where(
        Container.warehouse_id == warehouse_id, Container.id.in_(ids)
    )).scalars().all() if ids else []
    by_box = {str(x.id): x for x in boxes}
    routes = {}
    if ids and db.bind.dialect.name == 'postgresql' and db.execute(text("SELECT to_regclass('fc_box_routes')")).scalar():
        query=text("SELECT container_id,location_id,row_on_pallet,row_position FROM fc_box_routes WHERE warehouse_id=:w AND container_id IN :ids").bindparams(bindparam('ids',expanding=True))
        routes={str(x['container_id']):dict(x) for x in db.execute(query,{'w':str(warehouse_id),'ids':[str(x) for x in ids]}).mappings()}
    for place in receipt.get("places", []):
        address = location_label(by_id.get(str(place.get("location_id") or "")), by_id)
        route=routes.get(str(place.get('container_id') or ''),{})
        if route and str(route.get('location_id'))==str(place.get('location_id')):
            from app.services.r145f25_places import address_label
            address=address_label(by_id.get(str(place.get('location_id') or '')),by_id,route.get('row_on_pallet'),route.get('row_position'))
            place['row_on_pallet']=route.get('row_on_pallet');place['row_position']=route.get('row_position')
        place["location_display"] = address
        if place.get("target_kind") == "LOCATION":
            place["display_name"] = address
        else:
            box = by_box.get(str(place.get("container_id") or ""))
            place["display_name"] = str(getattr(box, "name", "") or place.get("target_code") or "Короб")
    return receipt
