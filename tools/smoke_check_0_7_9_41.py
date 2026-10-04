from pathlib import Path
root=Path(__file__).resolve().parents[1]
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
j=(root/'app/static/app.js').read_text(encoding='utf-8')
assert 'skipped_error_rows' in r
assert 'valid_rows = [row for row in rows if not row.get("errors")]' in r
assert 'Оприходовать / Добавить на остатки' in j
assert 'structured-bottom-commit' in j
assert 'Обновите страницу и выберите товар заново' in r
print("OK: FulfillCore 0.7.9.42 partial structured receiving + delete fallback enabled")
