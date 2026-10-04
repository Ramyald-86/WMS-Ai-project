from pathlib import Path
import re, shutil, sys, time
root = Path('/opt/fulfillcore')
main = root/'app/main.py'
js = root/'app/static/app.js'
downloads = root/'app/downloads'
static = root/'app/static'
bundle = Path(__file__).resolve().parent/'files'
for p in (main, js, downloads, static):
    if not p.exists():
        raise SystemExit(f'MISSING: {p}')
ts=time.strftime('%Y%m%d_%H%M%S')
backup=root/f'backup_print_agent_site_1_2_5_{ts}'
backup.mkdir(parents=True)
for p in (main, js): shutil.copy2(p, backup/p.name)
if (static/'print-agent.html').exists(): shutil.copy2(static/'print-agent.html', backup/'print-agent.html')
for p in downloads.glob('FulfillCore_Print_Agent_*'): 
    if p.is_file(): shutil.copy2(p, backup/p.name)

new_archive='FulfillCore_Print_Agent_1.2.5_ONE_CLICK_BACKGROUND_RELIABLE.zip'
new_instruction='FulfillCore_Print_Agent_1.2.5_Инструкция.txt'
shutil.copy2(bundle/new_archive, downloads/new_archive)
shutil.copy2(bundle/new_instruction, downloads/new_instruction)
shutil.copy2(bundle/'print-agent.html', static/'print-agent.html')

s=main.read_text(encoding='utf-8')
s2=re.sub(r'PRINT_AGENT_ARCHIVE\s*=\s*DOWNLOADS_DIR\s*/\s*"[^"]+"', f'PRINT_AGENT_ARCHIVE = DOWNLOADS_DIR / "{new_archive}"', s, count=1)
s2=re.sub(r'PRINT_AGENT_INSTRUCTION\s*=\s*DOWNLOADS_DIR\s*/\s*"[^"]+"', f'PRINT_AGENT_INSTRUCTION = DOWNLOADS_DIR / "{new_instruction}"', s2, count=1)
if s2==s: raise SystemExit('Could not patch Print Agent download constants in app/main.py')
main.write_text(s2,encoding='utf-8')

j=js.read_text(encoding='utf-8')
j2=re.sub(r'PRINT AGENT 1\.2\.\d+', 'PRINT AGENT 1.2.5', j)
if j2==j:
    print('WARN: app.js version badge was not found; download page will still work.')
else:
    js.write_text(j2,encoding='utf-8')
print('OK: host files patched')
print('BACKUP:', backup)
