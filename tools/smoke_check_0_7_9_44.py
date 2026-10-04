from pathlib import Path
root=Path(__file__).resolve().parents[1]
j=(root/'app/static/app.js').read_text(encoding='utf-8')
assert 'Excel: неподгруженные' in j
assert 'data-structured-unloaded' in j
assert 'Показать список WB FBS' not in j
assert 'Скрыть список' not in j
assert 'id="structuredFbsPanel"' not in j
print("OK: FulfillCore 0.7.9.44 structured FBS list removed from UI")
