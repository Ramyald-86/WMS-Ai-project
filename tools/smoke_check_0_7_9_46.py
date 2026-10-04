from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
j=(root/'app/static/app.js').read_text(encoding='utf-8')
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
assert 'version="0.7.9.46"' in main
assert 'FC_BUILD_07946' in j
assert 'Показать список WB FBS' not in j
assert 'id="structuredFbsPanel"' not in j
assert 'FCBOX-' in r
assert 'fulfillcore-box:{client_id}:{warehouse_id}:{box}' in r
print("OK: HOST files are 0.7.9.46. Run tools/deploy_live_0_7_9_46.sh to verify the LIVE container too.")
