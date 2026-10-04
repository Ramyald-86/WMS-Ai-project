@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$shell=New-Object -ComObject WScript.Shell; $lnk=$shell.CreateShortcut([Environment]::GetFolderPath('Startup')+'\FulfillCore Print Agent.lnk'); $lnk.TargetPath='%~dp0start_print_agent.bat'; $lnk.WorkingDirectory='%~dp0'; $lnk.Save()"
echo Print Agent добавлен в автозагрузку Windows.
pause
