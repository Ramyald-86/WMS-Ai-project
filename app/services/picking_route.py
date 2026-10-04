"""Pure route rules: complete a side across a section before changing sides."""
import re


def natural(value):
    return tuple((1, int(p)) if p.isdigit() else (0, p.casefold())
                 for p in re.split(r'(\d+)', str(value or '')) if p)


def location_route(location_id, locations):
    """Use the actual parent tree. Never infer a side from a box name."""
    loc = locations.get(str(location_id)) or {}
    section = None; pallet = None; seen = set(); node = loc
    while node and str(node.get('id')) not in seen:
        seen.add(str(node.get('id')))
        kind = str(node.get('location_type') or '').upper()
        if pallet is None and kind in ('PALLET', 'PALLET_PLACE'):
            pallet = node
        if kind == 'SECTION':
            section = node; break
        node = locations.get(str(node.get('parent_id'))) or {}
    section_code = str((section or {}).get('code') or '')
    pallet_code = str((pallet or {}).get('code') or '')
    return {'route_section': section_code.removeprefix('SEC-'),
            'route_section_id': str((section or {}).get('id') or ''),
            'route_pallet': pallet_code, 'route_pallet_id': str((pallet or {}).get('id') or ''),
            'route_row': None, 'route_position': None}


def route_key(row):
    section = row.get('route_section') or ''
    side = row.get('route_row')
    known = bool(section) and (isinstance(side,int) and side > 0)
    return (0 if section else 1, natural(section), 0 if known else 1, side if known else 99,
            natural(row.get('route_pallet') or row.get('location_code') or row.get('location')),
            side if (isinstance(side,int) and side > 0) else 99,
            int(row.get('route_position') or 99),
            natural(row.get('container_name') or row.get('container_barcode') or row.get('container')),
            natural(row.get('article')), natural(row.get('size') or row.get('sku_size')),
            str(row.get('barcode') or ''), str(row.get('location_id') or ''),
            str(row.get('container_id') or ''), str(row.get('id') or ''))


def route_label(row):
    from app.services.r145f25_places import public_code
    parts=[]
    if row.get('route_section'):
        sec=str(row['route_section']);parts.append(sec if sec.upper().startswith('SEC-') else 'SEC-'+(sec.zfill(3) if sec.isdigit() else sec))
    if row.get('route_row'):parts.append('\u0420\u044f\u0434 '+str(row['route_row']))
    if row.get('route_pallet'):parts.append(public_code({'code':str(row['route_pallet'])}))
    elif row.get('location_code'):parts.append(public_code({'code':str(row['location_code'])}))
    if row.get('route_position'):parts.append('\u041c\u0435\u0441\u0442\u043e '+str(row['route_position']))
    if not row.get('route_row') and row.get('route_pallet'):parts.append('\u0420\u044f\u0434 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d')
    return ' \u2192 '.join(parts)


def choose_position(occupied):
    for pos in range(1, 9):
        if pos not in occupied: return pos
    raise ValueError('В этом ряду палеты уже 8 коробов. Выберите другой ряд или палету; '
                     'убранный пустой короб переместите или архивируйте.')
