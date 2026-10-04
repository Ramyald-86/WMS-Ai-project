from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
checks={
    'version':'version="0.7.9.20"' in main,
    'partial_hint':'Можно указать часть количества' in js,
    'quantity_first':js.find('Сколько разместить?') < js.find('Куда разместить ${fmtQty(qty)} шт.?'),
    'limit_check':'qty>available' in js,
    'remainder':'В ПРИЕМКА осталось ${fmtQty(remainder)} шт.' in js,
    'move_api':"api('/operations/move'" in js,
    'partial_comment':'Частичное размещение из виртуальной ПРИЕМКА' in js,
}
failed=[name for name,ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAILED: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.20 partial virtual receiving placement smoke-check passed')
