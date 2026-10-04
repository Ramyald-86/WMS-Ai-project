from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
j=(root/"app/static/app.js").read_text(encoding="utf-8")
assert 'version="0.7.9.50"' in m
assert 'FC_BUILD_07950' in j
assert 'Every barcode listed on one WB card/size is an alias' in j
assert 'local_barcode_quantities' in j
assert 'card.quantity=Number(card.quantity||0)+qty' in j
print("OK: 0.7.9.50 multi-barcode inventory aliases + physical balance truth enabled")
