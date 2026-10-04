import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, text

from app.models import SKU

# pypdf uses absolute imports internally (``from pypdf...``).  The release bundles
# it under app/vendor, so put that directory on sys.path and import it by its real
# top-level package name.  This keeps the runtime independent of pip/network installs.
import sys
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parents[1] / 'vendor'
_vendor_dir_text = str(_VENDOR_DIR)
if _vendor_dir_text not in sys.path:
    sys.path.insert(0, _vendor_dir_text)
from pypdf import PdfReader, PdfWriter


_SCHEMA_READY = False

SCHEMA_TABLES = (
    'fc_pvz_batches', 'fc_pvz_lines', 'fc_pvz_label_batches', 'fc_pvz_label_pages',
    'fc_pvz_print_jobs', 'fc_client_portal_users',
)


def install_schema(db) -> None:
    """Additive r91 schema only; safe to run after the release backup exists."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    existing = [db.execute(text('SELECT to_regclass(:name)'), {'name': name}).scalar() for name in SCHEMA_TABLES]
    if all(existing):
        _SCHEMA_READY = True
        return
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_pvz_batches (
          id UUID PRIMARY KEY,
          client_id UUID NOT NULL,
          warehouse_id UUID NOT NULL,
          kind VARCHAR(16) NOT NULL CHECK (kind IN ('GOOD','DEFECT')),
          status VARCHAR(24) NOT NULL DEFAULT 'COMPLETED',
          reference TEXT NOT NULL DEFAULT '',
          performed_by TEXT NOT NULL DEFAULT '',
          stock_operation_id UUID NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    '''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_batches_scope ON fc_pvz_batches(client_id,warehouse_id,created_at DESC)'))
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_pvz_lines (
          id UUID PRIMARY KEY,
          batch_id UUID NOT NULL REFERENCES fc_pvz_batches(id) ON DELETE CASCADE,
          sku_id UUID NULL,
          barcode VARCHAR(120) NOT NULL DEFAULT '',
          article VARCHAR(180) NOT NULL DEFAULT '',
          size VARCHAR(100) NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT '',
          quantity NUMERIC(18,3) NOT NULL CHECK (quantity>0),
          location_id UUID NULL,
          container_id UUID NULL,
          defect_reason TEXT NOT NULL DEFAULT '',
          comment TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    '''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_lines_batch ON fc_pvz_lines(batch_id)'))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_lines_sku ON fc_pvz_lines(sku_id)'))
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_pvz_label_batches (
          id UUID PRIMARY KEY,
          client_id UUID NOT NULL,
          warehouse_id UUID NOT NULL,
          original_filename TEXT NOT NULL,
          pdf_bytes BYTEA NOT NULL,
          pdf_sha256 VARCHAR(64) NOT NULL,
          page_count INTEGER NOT NULL,
          performed_by TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    '''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_label_batches_scope ON fc_pvz_label_batches(client_id,warehouse_id,created_at DESC)'))
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_pvz_label_pages (
          id BIGSERIAL PRIMARY KEY,
          batch_id UUID NOT NULL REFERENCES fc_pvz_label_batches(id) ON DELETE CASCADE,
          page_no INTEGER NOT NULL,
          article VARCHAR(180) NOT NULL DEFAULT '',
          size VARCHAR(100) NOT NULL DEFAULT '',
          barcode_value VARCHAR(160) NOT NULL DEFAULT '',
          label_type VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' CHECK (label_type IN ('BARCODE','CHZ','UNKNOWN')),
          extracted_text TEXT NOT NULL DEFAULT '',
          page_key VARCHAR(64) NOT NULL,
          UNIQUE(batch_id,page_no)
        )
    '''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_label_pages_group ON fc_pvz_label_pages(batch_id,article,size,label_type,page_no)'))
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_pvz_print_jobs (
          id UUID PRIMARY KEY,
          batch_id UUID NOT NULL REFERENCES fc_pvz_label_batches(id) ON DELETE CASCADE,
          client_id UUID NOT NULL,
          warehouse_id UUID NOT NULL,
          article VARCHAR(180) NOT NULL DEFAULT '',
          size VARCHAR(100) NOT NULL DEFAULT '',
          barcode_copies INTEGER NOT NULL DEFAULT 0,
          chz_count INTEGER NOT NULL DEFAULT 0,
          chz_start INTEGER NOT NULL DEFAULT 1,
          performed_by TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    '''))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_pvz_print_jobs_scope ON fc_pvz_print_jobs(client_id,warehouse_id,created_at DESC)'))
    db.execute(text('''
        CREATE TABLE IF NOT EXISTS fc_client_portal_users (
          id UUID PRIMARY KEY,
          client_id UUID NOT NULL,
          username VARCHAR(120) NOT NULL,
          password_salt VARCHAR(64) NOT NULL,
          password_hash VARCHAR(128) NOT NULL,
          is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    '''))
    db.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ux_fc_client_portal_users_username_lower ON fc_client_portal_users(lower(username))'))
    db.execute(text('CREATE INDEX IF NOT EXISTS ix_fc_client_portal_users_client ON fc_client_portal_users(client_id)'))
    _SCHEMA_READY = True


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64d(value: str) -> bytes:
    value += '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode('ascii'))


def password_parts(password: str, *, salt: str | None = None) -> tuple[str, str]:
    password = str(password or '')
    if len(password) < 8:
        raise ValueError('Пароль клиента должен содержать минимум 8 символов')
    raw_salt = _b64d(salt) if salt else secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), raw_salt, 210_000)
    return _b64e(raw_salt), _b64e(digest)


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    try:
        _, actual = password_parts(password, salt=salt)
    except Exception:
        return False
    return hmac.compare_digest(actual, str(expected_hash or ''))


def make_portal_token(secret: str, user_id: str, client_id: str, username: str, *, ttl_seconds: int = 60 * 60 * 24 * 7) -> str:
    payload = {
        'v': 1, 'uid': str(user_id), 'cid': str(client_id), 'u': str(username),
        'exp': int(time.time()) + int(ttl_seconds),
    }
    body = _b64e(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    sig = _b64e(hmac.new(str(secret).encode('utf-8'), body.encode('ascii'), hashlib.sha256).digest())
    return body + '.' + sig


def read_portal_token(secret: str, value: str | None) -> dict | None:
    try:
        body, sig = str(value or '').split('.', 1)
        expected = _b64e(hmac.new(str(secret).encode('utf-8'), body.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(body).decode('utf-8'))
        if int(data.get('exp') or 0) < int(time.time()):
            return None
        uuid.UUID(str(data['uid'])); uuid.UUID(str(data['cid']))
        return data
    except Exception:
        return None


def normalize_pdf_text(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _token_match(haystack: str, needle: str) -> bool:
    needle = str(needle or '').strip()
    if not needle:
        return False
    return re.search(r'(?<![0-9A-ZА-ЯЁ])' + re.escape(needle.upper()) + r'(?![0-9A-ZА-ЯЁ])', haystack.upper()) is not None


def _digit_text(value: str) -> str:
    return re.sub(r'\D+', '', str(value or ''))


def _catalog_rows(db, client_id) -> list[dict]:
    rows = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.is_active == True)).scalars().all()  # noqa: E712
    result = []
    for sku in rows:
        barcode = str(sku.barcode or '').strip()
        article = str(sku.article or '').strip()
        size = str(sku.size or '').strip()
        if barcode or article:
            result.append({'id': str(sku.id), 'barcode': barcode, 'article': article, 'size': size, 'name': str(sku.name or '')})
    result.sort(key=lambda x: (len(x['article']), len(x['barcode'])), reverse=True)
    return result


def _identify_page(text_value: str, catalog: list[dict], previous: dict | None = None) -> dict:
    text_value = normalize_pdf_text(text_value)
    upper = text_value.upper()
    digits = _digit_text(text_value)
    matched = None
    for item in catalog:
        code_digits = _digit_text(item['barcode'])
        if code_digits and len(code_digits) >= 6 and code_digits in digits:
            matched = item
            break
    if matched is None:
        for item in catalog:
            article = item['article']
            size = item['size']
            if article and article.upper() in upper and (not size or _token_match(upper, size)):
                matched = item
                break
    chz_words = ('ЧЕСТНЫЙ ЗНАК', 'КОД МАРКИРОВКИ', 'DATAMATRIX', 'DATA MATRIX', 'КИЗ', 'МАРКИРОВК')
    gs1 = bool(re.search(r'(?:^|\D)01\d{14}21[0-9A-ZА-ЯЁ]', upper.replace(' ', '')))
    if any(word in upper for word in chz_words) or gs1:
        kind = 'CHZ'
    elif matched and matched.get('barcode'):
        kind = 'BARCODE'
    else:
        kind = 'UNKNOWN'
    if matched:
        article, size, barcode = matched['article'], matched['size'], matched['barcode']
    else:
        article = size = barcode = ''
        # Unique CHZ pages often do not repeat the readable article/size. If the file is
        # grouped by product, inherit only for CHZ/unknown pages immediately following a
        # confidently identified product page. The UI still allows manual correction.
        if previous and previous.get('article') and kind != 'BARCODE':
            article, size, barcode = previous.get('article',''), previous.get('size',''), previous.get('barcode_value','')
            if kind == 'UNKNOWN':
                kind = 'CHZ'
    if not barcode:
        candidates = re.findall(r'(?<!\d)\d{8,20}(?!\d)', text_value.replace(' ', ''))
        barcode = candidates[0] if candidates else ''
    return {'article': article, 'size': size, 'barcode_value': barcode, 'label_type': kind, 'text': text_value}


def analyze_pdf(db, client_id, pdf_bytes: bytes) -> list[dict]:
    if not pdf_bytes.startswith(b'%PDF'):
        raise ValueError('Файл не похож на PDF')
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) < 1:
        raise ValueError('PDF не содержит страниц')
    if len(reader.pages) > 5000:
        raise ValueError('В одном PDF допускается не более 5000 страниц')
    catalog = _catalog_rows(db, client_id)
    result = []
    previous = None
    for index, page in enumerate(reader.pages, start=1):
        try:
            extracted = page.extract_text() or ''
        except Exception:
            extracted = ''
        identified = _identify_page(extracted, catalog, previous)
        identified['page_no'] = index
        identified['page_key'] = hashlib.sha256((str(index) + '\n' + identified['text']).encode('utf-8', errors='replace')).hexdigest()
        result.append(identified)
        if identified.get('article'):
            previous = identified
    return result


def build_pdf(pdf_bytes: bytes, barcode_pages: Iterable[int], barcode_copies: int, chz_pages: Iterable[int], chz_count: int, chz_start: int = 1) -> bytes:
    barcode_copies = int(barcode_copies or 0)
    chz_count = int(chz_count or 0)
    chz_start = max(1, int(chz_start or 1))
    if barcode_copies < 0 or barcode_copies > 5000 or chz_count < 0 or chz_count > 5000:
        raise ValueError('За одну печать допускается не более 5000 этикеток каждого типа')
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    bpages = [int(x) for x in barcode_pages]
    cpages = [int(x) for x in chz_pages]
    if barcode_copies:
        if not bpages:
            raise ValueError('Для этой позиции в PDF не найдена страница ШК')
        # A product barcode label is repeatable. If the supplier provided several barcode
        # templates, cycle through them instead of silently dropping the later pages.
        for i in range(barcode_copies):
            writer.add_page(reader.pages[bpages[i % len(bpages)] - 1])
    if chz_count:
        start = chz_start - 1
        selected = cpages[start:start + chz_count]
        if len(selected) != chz_count:
            raise ValueError(f'Уникальных ЧЗ недостаточно: с позиции {chz_start} доступно {max(0, len(cpages)-start)}, нужно {chz_count}')
        for page_no in selected:
            writer.add_page(reader.pages[page_no - 1])
    if barcode_copies + chz_count <= 0:
        raise ValueError('Укажите количество ШК или ЧЗ для печати')
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def group_label_pages(rows: list[dict], stock_by_identity: dict[tuple[str, str], float] | None = None) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get('article') or ''), str(row.get('size') or ''))
        g = grouped.setdefault(key, {'article': key[0], 'size': key[1], 'barcodes': set(), 'barcode_pages': [], 'chz_pages': [], 'unknown_pages': [], 'stock_qty': 0})
        if row.get('barcode_value'):
            g['barcodes'].add(str(row['barcode_value']))
        if row.get('label_type') == 'BARCODE': g['barcode_pages'].append(int(row['page_no']))
        elif row.get('label_type') == 'CHZ': g['chz_pages'].append(int(row['page_no']))
        else: g['unknown_pages'].append(int(row['page_no']))
    stock_by_identity = stock_by_identity or {}
    out=[]
    for key,g in grouped.items():
        g['barcodes']=sorted(g['barcodes'])
        g['stock_qty']=float(stock_by_identity.get(key,0) or 0)
        out.append(g)
    return sorted(out,key=lambda x:(not bool(x['article']),x['article'],x['size']))


def stock_snapshot(db, client_id, warehouse_id=None) -> list[dict]:
    params = {'c': str(client_id)}
    wh_clause = ''
    if warehouse_id:
        params['w'] = str(warehouse_id)
        wh_clause = ' AND CAST(b.warehouse_id AS varchar)=:w '
    rows = db.execute(text(f'''
      SELECT CAST(b.warehouse_id AS varchar) warehouse_id, COALESCE(w.name,CAST(b.warehouse_id AS varchar)) warehouse_name,
        CAST(b.sku_id AS varchar) sku_id, COALESCE(s.barcode,'') barcode,COALESCE(s.article,'') article,
        COALESCE(s.size,'') size,COALESCE(s.name,'') name,
        SUM(GREATEST(b.quantity,0)) physical,
        SUM(GREATEST(COALESCE(sv.storage_only,0),0)) storage_only,
        SUM(GREATEST(COALESCE(sv.fbs_available,0),0)) fbs_available,
        SUM(GREATEST(COALESCE(sv.reserved,0),0)) reserved
      FROM inventory_balances b
      JOIN skus s ON CAST(s.id AS varchar)=CAST(b.sku_id AS varchar)
      LEFT JOIN warehouses w ON CAST(w.id AS varchar)=CAST(b.warehouse_id AS varchar)
      LEFT JOIN fc_stocks_v2 sv ON CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
        AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
        AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
        AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
        AND COALESCE(sv.container_key,'')=COALESCE(CAST(b.container_id AS varchar),'')
      WHERE CAST(b.client_id AS varchar)=:c AND b.quantity>0 {wh_clause}
        AND s.is_active=TRUE
      GROUP BY b.warehouse_id,w.name,b.sku_id,s.barcode,s.article,s.size,s.name
      HAVING SUM(GREATEST(b.quantity,0))>0
      ORDER BY w.name,s.article,s.size,s.barcode
    '''), params).mappings().all()
    return [dict(r) for r in rows]


def stock_by_article_size(db, client_id, warehouse_id) -> dict[tuple[str,str], float]:
    out: dict[tuple[str,str],float] = {}
    for row in stock_snapshot(db, client_id, warehouse_id):
        key=(str(row.get('article') or ''),str(row.get('size') or ''))
        out[key]=out.get(key,0.0)+float(row.get('physical') or 0)
    return out


def stock_locations(db, client_id, sku_id, warehouse_id=None) -> list[dict]:
    """Physical placement breakdown for the client portal.

    Uses only public warehouse labels (warehouse/location/box name). Internal UUIDs are
    returned only as opaque keys for the UI and are never displayed to the client.
    """
    params={'c':str(client_id),'s':str(sku_id)}
    wh=''
    if warehouse_id:
        params['w']=str(warehouse_id);wh=' AND CAST(b.warehouse_id AS varchar)=:w '
    rows=db.execute(text(f'''
      SELECT CAST(b.warehouse_id AS varchar) warehouse_id,
        COALESCE(w.name,CAST(b.warehouse_id AS varchar)) warehouse_name,
        CAST(b.sku_id AS varchar) sku_id,
        COALESCE(l.code,'') location,
        COALESCE(c.name,c.barcode,'') container,
        SUM(GREATEST(b.quantity,0)) physical,
        SUM(GREATEST(COALESCE(sv.storage_only,0),0)) storage_only,
        SUM(GREATEST(COALESCE(sv.fbs_available,0),0)) fbs_available,
        SUM(GREATEST(COALESCE(sv.reserved,0),0)) reserved
      FROM inventory_balances b
      JOIN skus s ON CAST(s.id AS varchar)=CAST(b.sku_id AS varchar)
      LEFT JOIN warehouses w ON CAST(w.id AS varchar)=CAST(b.warehouse_id AS varchar)
      LEFT JOIN locations l ON CAST(l.id AS varchar)=CAST(b.location_id AS varchar)
      LEFT JOIN containers c ON CAST(c.id AS varchar)=CAST(b.container_id AS varchar)
      LEFT JOIN fc_stocks_v2 sv ON CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
        AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
        AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
        AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
        AND COALESCE(sv.container_key,'')=COALESCE(CAST(b.container_id AS varchar),'')
      WHERE CAST(b.client_id AS varchar)=:c AND CAST(b.sku_id AS varchar)=:s
        AND b.quantity>0 {wh} AND s.is_active=TRUE
      GROUP BY b.warehouse_id,w.name,b.sku_id,l.code,c.name,c.barcode
      HAVING SUM(GREATEST(b.quantity,0))>0
      ORDER BY w.name,l.code,COALESCE(c.name,c.barcode,'')
    '''),params).mappings().all()
    return [dict(r) for r in rows]


def stock_locations_all(db, client_id) -> list[dict]:
    rows=db.execute(text('''
      SELECT CAST(b.warehouse_id AS varchar) warehouse_id,
        COALESCE(w.name,CAST(b.warehouse_id AS varchar)) warehouse_name,
        CAST(b.sku_id AS varchar) sku_id,COALESCE(s.barcode,'') barcode,
        COALESCE(s.article,'') article,COALESCE(s.size,'') size,COALESCE(s.name,'') name,
        COALESCE(l.code,'') location,COALESCE(c.name,c.barcode,'') container,
        SUM(GREATEST(b.quantity,0)) physical,
        SUM(GREATEST(COALESCE(sv.storage_only,0),0)) storage_only,
        SUM(GREATEST(COALESCE(sv.fbs_available,0),0)) fbs_available,
        SUM(GREATEST(COALESCE(sv.reserved,0),0)) reserved
      FROM inventory_balances b
      JOIN skus s ON CAST(s.id AS varchar)=CAST(b.sku_id AS varchar)
      LEFT JOIN warehouses w ON CAST(w.id AS varchar)=CAST(b.warehouse_id AS varchar)
      LEFT JOIN locations l ON CAST(l.id AS varchar)=CAST(b.location_id AS varchar)
      LEFT JOIN containers c ON CAST(c.id AS varchar)=CAST(b.container_id AS varchar)
      LEFT JOIN fc_stocks_v2 sv ON CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
        AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
        AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
        AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
        AND COALESCE(sv.container_key,'')=COALESCE(CAST(b.container_id AS varchar),'')
      WHERE CAST(b.client_id AS varchar)=:c AND b.quantity>0 AND s.is_active=TRUE
      GROUP BY b.warehouse_id,w.name,b.sku_id,s.barcode,s.article,s.size,s.name,l.code,c.name,c.barcode
      HAVING SUM(GREATEST(b.quantity,0))>0
      ORDER BY w.name,s.article,s.size,s.barcode,l.code,COALESCE(c.name,c.barcode,'')
    '''),{'c':str(client_id)}).mappings().all()
    return [dict(r) for r in rows]
