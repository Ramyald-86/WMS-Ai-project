from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
j=(root/"app/static/app.js").read_text(encoding="utf-8")
assert "0.7.9.47" in m
assert "0.7.9.42" not in m
assert "FC_BUILD_07947" in j
assert "Показать список WB FBS" not in j
print("OK: host build 0.7.9.47 health/frontend sync prepared")
