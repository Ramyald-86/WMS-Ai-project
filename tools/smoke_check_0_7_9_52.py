from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
j=(root/"app/static/app.js").read_text(encoding="utf-8")
r=(root/"app/routers/receiving.py").read_text(encoding="utf-8")
assert 'version="0.7.9.52"' in m
assert 'FC_BUILD_07952' in j
assert '@router.get("/bulk/recent")' in r
assert 'Последние массовые приёмки' in j
assert 'Отменить приёмку' in j
print("OK: 0.7.9.52 bulk receiving history + selective cancel enabled")
