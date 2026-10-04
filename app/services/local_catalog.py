"""Catalog stored in the WMS database. This module never calls a marketplace API."""
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import load_only
from app.models import MarketplaceProduct, SKU
from app.services.local_catalog_rules import combine_catalog


def catalog(db, client_id, code=None):
    sku_query = select(SKU).where(SKU.client_id == client_id, SKU.is_active == True)
    card_query = select(MarketplaceProduct).options(load_only(
        MarketplaceProduct.id, MarketplaceProduct.vendor_code, MarketplaceProduct.title,
        MarketplaceProduct.brand, MarketplaceProduct.photos, MarketplaceProduct.sizes,
        MarketplaceProduct.external_product_id, MarketplaceProduct.updated_at,
        MarketplaceProduct.created_at)).where(
        MarketplaceProduct.client_id == client_id, MarketplaceProduct.provider == 'WB')
    if code is not None:
        sku_query = sku_query.where(SKU.barcode == code)
        card_query = card_query.where(cast(MarketplaceProduct.barcodes, JSONB).contains([code]))
    skus = db.execute(sku_query).scalars().all()
    products = db.execute(card_query.order_by(MarketplaceProduct.updated_at.desc(),
                                              MarketplaceProduct.created_at.desc())).scalars().all()
    local = [{'sku_id': str(s.id), 'barcode': s.barcode, 'article': s.article or '',
              'size': s.size or '', 'name': s.name or ''} for s in skus]
    cards = []
    for p in products:
        photos = p.photos or []
        photo = photos[0] if photos else ''
        if isinstance(photo, dict):
            photo = photo.get('big') or photo.get('c246x328') or photo.get('square') or ''
        cards.append({'product_id': str(p.id), 'nm_id': p.external_product_id,
                      'article': p.vendor_code or '', 'name': p.title or '',
                      'brand': p.brand or '', 'photo': str(photo), 'sizes': p.sizes or []})
    return [r for r in combine_catalog(local, cards) if code is None or r['barcode'] == code]
