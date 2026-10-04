$ErrorActionPreference = "Stop"
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $base "config.json"
if (!(Test-Path $configPath)) {
    Write-Host "Не найден config.json. Скопируйте config.example.json в config.json и заполните настройки." -ForegroundColor Red
    Read-Host "Enter для выхода"
    exit 1
}
$config = Get-Content $configPath -Raw | ConvertFrom-Json
$server = ($config.server_url).TrimEnd('/')
$station = ($config.station_code).ToUpper()
$printer = $config.printer_name
$sumatra = $config.sumatra_path
$poll = [int]$config.poll_seconds
if ($poll -lt 2) { $poll = 2 }

$headers = @{}
if ($config.agent_token) {
    $headers["X-Print-Agent-Token"] = $config.agent_token
}

if (!(Test-Path $sumatra)) {
    Write-Host "SumatraPDF не найден: $sumatra" -ForegroundColor Yellow
    Write-Host "Установите SumatraPDF или укажите правильный путь в config.json."
    Read-Host "Enter для выхода"
    exit 1
}

Write-Host "FulfillCore Print Agent" -ForegroundColor Cyan
Write-Host "Сервер: $server"
Write-Host "Рабочее место: $station"
Write-Host "Принтер: $printer"
Write-Host "Ожидание заданий печати... Ctrl+C для остановки."

while ($true) {
    try {
        $url = "$server/printing/agent/next?station_code=$([uri]::EscapeDataString($station))"
        $resp = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -TimeoutSec 15
        if ($null -eq $resp.job) {
            Start-Sleep -Seconds $poll
            continue
        }

        $job = $resp.job
        $safeName = [IO.Path]::GetFileName($job.file_name)
        if (!$safeName) { $safeName = "$($job.id).pdf" }
        $temp = Join-Path $env:TEMP ("FulfillCore_" + $job.id + "_" + $safeName)
        [IO.File]::WriteAllBytes($temp, [Convert]::FromBase64String($job.content_base64))

        try {
            if ($printer) {
                $args = @("-print-to", $printer, "-silent", $temp)
            } else {
                $args = @("-print-to-default", "-silent", $temp)
            }
            $proc = Start-Process -FilePath $sumatra -ArgumentList $args -PassThru -Wait -WindowStyle Hidden
            if ($proc.ExitCode -ne 0) { throw "SumatraPDF завершился с кодом $($proc.ExitCode)" }
            $body = @{ success = $true; error = $null } | ConvertTo-Json
            Invoke-RestMethod -Uri "$server/printing/agent/jobs/$($job.id)/result" -Headers $headers -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | Out-Null
            Write-Host "$(Get-Date -Format 'HH:mm:ss') Напечатано: $safeName" -ForegroundColor Green
        }
        catch {
            $err = $_.Exception.Message
            $body = @{ success = $false; error = $err } | ConvertTo-Json
            try { Invoke-RestMethod -Uri "$server/printing/agent/jobs/$($job.id)/result" -Headers $headers -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | Out-Null } catch {}
            Write-Host "Ошибка печати $safeName: $err" -ForegroundColor Red
        }
        finally {
            if (Test-Path $temp) { Remove-Item $temp -Force -ErrorAction SilentlyContinue }
        }
    }
    catch {
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Сервер недоступен: $($_.Exception.Message)" -ForegroundColor DarkYellow
        Start-Sleep -Seconds ([Math]::Max(3,$poll))
    }
}
