$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'okx_usdt_5m_monitor.py'

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source -3 --version *> $null
    if ($LASTEXITCODE -eq 0) {
        & $pythonCommand.Source -3 $scriptPath
        exit $LASTEXITCODE
    }
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw '找不到 Python 3。请先安装 Python 3.10 或更新版本。'
}
& $pythonCommand.Source --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw '当前 python 命令不可用。请安装 Python 3.10+ 并加入 PATH。'
}
& $pythonCommand.Source $scriptPath
exit $LASTEXITCODE
