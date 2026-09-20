# 从脚本目录定位 Skill；不依赖调用者当前目录。
param(
    [ValidateSet('inspect', 'init')][string]$Operation = 'inspect',
    [string]$Instance = '',
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathRooted($Python) -or -not (Test-Path -LiteralPath $Python)) {
    throw '请传入已识别的 Python 3.11+ 解释器绝对路径。'
}
$bridgeArguments = @((Join-Path $PSScriptRoot 'bridge_control.py'), $Operation)
if ($Instance) { $bridgeArguments += @('--instance', $Instance) }
$env:PYTHONUTF8 = '1'
& $Python @bridgeArguments
exit $LASTEXITCODE
