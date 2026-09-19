# 使用本项目虚拟环境启动，不影响其他项目的 Python 和代理设置。
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgePython = Join-Path $bridgeRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bridgePython)) { throw '项目虚拟环境不存在，请先按启动说明安装。' }
Set-Location -LiteralPath $bridgeRoot
$env:VIRTUAL_ENV = Join-Path $bridgeRoot '.venv'
$env:PATH = (Join-Path $env:VIRTUAL_ENV 'Scripts') + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
& $bridgePython -m goofish_bridge run
exit $LASTEXITCODE
