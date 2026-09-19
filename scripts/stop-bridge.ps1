# 请求主进程安全退出，保留未完成任务和引用映射。
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $bridgeRoot
$env:PYTHONUTF8 = '1'
& (Join-Path $bridgeRoot '.venv\Scripts\python.exe') -m goofish_bridge stop
exit $LASTEXITCODE
