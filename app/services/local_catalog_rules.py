"""Exact-barcode catalog enrichment. Never merge stock or change an SKU identity."""
import re


def barcode(value):
    code = str(value or '').strip()
    if not code or len(code) > 80 or re.search(r'[\s\x00-\x1f\x7f]', code):
        raise ValueError('Введите обычный ШК товара (до 80 знаков, без пробелов), не код маркировки')
    if code.startswith((']d2', ']C1', '(01)')) or (len(code) > 25 and code.startswith('01')):
        raise ValueError('Нужен ШК товара, не DataMatrix / Честный знак')
    if not re.fullmatch(r'(?:\d{8}|\d{12,14})', code):
        raise ValueError('Нужен обычный ШК товара: 8, 12, 13 или 14 цифр. Ведущие нули сохраняются')
    return code


def card_entries(cards):
    """Input is newest first; logical credential duplicates of one nm/size collapse."""
    found = {}
    for card in cards:
        sizes = card.get('sizes') or []
        for size in sizes:
            for raw in size.get('skus') or []:
                code = str(raw or '').strip()
                if not code:
                    continue
                signature = (str(card.get('nm_id') or ''), str(size.get('techSize') or ''),
                             str(card.get('article') or ''))
                entry = {**card, 'barcode': code, 'size': str(size.get('techSize') or ''),
                         'chrt_id': size.get('chrtID'), 'wb_found': True}
                entry.pop('sizes', None)
                bucket = found.setdefault(code, {})
                bucket.setdefault(signature, entry)
    result = {}
    for code, candidates in found.items():
        if len(candidates) == 1:
            result[code] = next(iter(candidates.values()))
        else:
            result[code] = {'barcode': code, 'wb_found': False, 'wb_conflict': True,
                            'warning': 'ШК встречается в разных карточках или размерах WB. Проверьте привязку.'}
    return result


def combine_catalog(local, cards):
    wb = card_entries(cards)
    grouped = {}
    for row in local:
        code = str(row.get('barcode') or '').strip()
        if code:
            grouped.setdefault(code, []).append(row)
    result = []
    for code in sorted(set(grouped) | set(wb)):
        matches = grouped.get(code, [])
        extra = wb.get(code, {})
        own = matches[0] if matches else {}
        row = {**extra, **own, 'barcode': code, 'found': bool(matches or extra.get('wb_found')),
               'wb_found': bool(extra.get('wb_found')), 'local': bool(matches),
               'source': 'LOCAL_WB' if matches and extra.get('wb_found') else 'LOCAL' if matches else 'WB'}
        # The local article/size identify an existing stock item. Imported WB metadata
        # fills blanks only; a discrepancy is visible and does not split the identity.
        for field in ('article', 'size', 'name'):
            row[field] = own.get(field) or extra.get(field) or ''
        for field in ('product_id', 'nm_id', 'photo', 'brand', 'wb_conflict', 'warning'):
            if field in extra:
                row[field] = extra[field]
        row['local_name'] = own.get('name') or ''
        row['name'] = extra.get('name') or own.get('name') or ''
        row['wb_article'] = extra.get('article') or ''
        row['wb_size'] = extra.get('size') or ''
        row['identity_difference'] = any(own.get(k) and extra.get(k) and
            str(own[k]).strip().casefold() != str(extra[k]).strip().casefold() for k in ('article', 'size'))
        row['sku_ids'] = [str(x['sku_id']) for x in matches]
        row['duplicate_sku'] = len(matches) > 1
        if row['duplicate_sku']:
            row['warning'] = 'ШК привязан к нескольким SKU. Проверьте справочник перед приёмкой.'
        result.append(row)
    return result
