from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
recv=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
identity=(root/'app/services/product_identity.py').read_text(encoding='utf-8')
assert 'version="0.7.9.42"' in main
assert '"chrtIds": chunk' in recv
assert '{"chrtId": int(chrt_id), "amount": max(0, int(amount))}' in recv
assert '_wb_write_and_verify_chrt_stocks' in recv
assert 'WB не подтвердил остаток после записи' in recv
assert '"chrt_id": _product_chrt_id' in identity
assert '"skus": [str(barcode)]' not in recv
assert '"sku": str(barcode), "amount"' not in recv
print('OK: FulfillCore 0.7.9.42 WB FBS stock sync uses chrtId and read-after-write verification')
