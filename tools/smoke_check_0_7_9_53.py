from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
j=(root/"app/static/app.js").read_text(encoding="utf-8")
r=(root/"app/routers/receiving.py").read_text(encoding="utf-8")
assert 'version="0.7.9.53"' in m
assert 'FC_BUILD_07953' in j
assert '@router.get("/bulk/{batch_id}/errors.xlsx")' in r
assert '@router.get("/bulk/{batch_id}/detail")' in r
assert 'Скачать Excel ошибок' in j
assert 'Принять на остатки WB FBS' in j
assert 'const publishNow=false' in j
print("OK: 0.7.9.53 local-first receiving + errors Excel + manual WB button enabled")
