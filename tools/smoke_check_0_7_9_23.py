from pathlib import Path

root = Path(__file__).resolve().parents[1]
receiving = (root / 'app/routers/receiving.py').read_text(encoding='utf-8')
js = (root / 'app/static/app.js').read_text(encoding='utf-8')
main = (root / 'app/main.py').read_text(encoding='utf-8')

checks = {
    'version': 'version="0.7.9.23"' in main,
    'internal flag': 'internal_only: bool = False' in receiving,
    'virtual receiving': 'destination": "ПРИЕМКА" if payload.internal_only' in receiving,
    'no wb pending internal': 'if not payload.internal_only:' in receiving,
    'xlsx export route': '@router.post("/manual/unmatched-export.xlsx")' in receiving,
    'wb_found': '"wb_found": bool(product)' in receiving,
    'accept known only': "allRows.filter(row=>row.product_found!==false)" in js,
    'keep unmatched': "draft.rows=allRows.filter(row=>row.product_found===false)" in js,
    'unmatched excel': 'Excel: не найдено в WB' in js,
    'internal button': 'Принять не найденные во внутренний учёт' in js,
    'internal batch': 'internal_only:true' in js,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.23 unmatched-WB internal-receiving smoke-check passed')
