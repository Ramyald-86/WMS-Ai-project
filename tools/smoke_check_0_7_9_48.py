from pathlib import Path
root = Path(__file__).resolve().parents[1]
main = (root/"app/main.py").read_text(encoding="utf-8")
js = (root/"app/static/app.js").read_text(encoding="utf-8")
receiving = (root/"app/routers/receiving.py").read_text(encoding="utf-8")
assert 'version="0.7.9.48"' in main
assert 'FC_BUILD_07948' in js
assert 'Показать список WB FBS' not in js
assert 'FCBOX-' in receiving
print("OK: slim host hotfix 0.7.9.48 is ready")
