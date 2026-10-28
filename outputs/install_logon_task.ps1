$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'start_monitor.ps1'
$taskName = 'Codex-OKX-USDT-5m-Monitor'
$powerShell = Join-Path $PSHOME 'powershell.exe'
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powerShell -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`"" -WorkingDirectory $PSScriptRoot -ErrorAction Stop
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId -ErrorAction Stop
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 3650) -ErrorAction Stop
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited -ErrorAction Stop
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Starts the read-only OKX USDT perpetual 5-minute market monitor at user logon.' -Force -ErrorAction Stop | Out-Null
Write-Host "已创建登录启动任务：$taskName"
Write-Host '任务运行后，监控进程会每 5 分钟读取一次公开 OKX 行情。'
Write-Host "报告文件：$(Join-Path $PSScriptRoot 'monitor_data\latest_report.md')"
