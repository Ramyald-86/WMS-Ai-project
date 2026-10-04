from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
j=(root/"app/static/app.js").read_text(encoding="utf-8")
c=(root/"app/routers/commercial.py").read_text(encoding="utf-8")
assert 'version="0.8.0"' in m
assert 'FC_BUILD_0800_COMMERCIAL' in j
assert 'fc_fulfillment_tenants' in c
assert '/admin/tenants' in c
assert 'subscription_until' in c
assert 'commercial-login.html' in m
print("OK: FulfillCore 0.8.0 Commercial files ready")
