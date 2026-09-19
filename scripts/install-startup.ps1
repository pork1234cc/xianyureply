# 显式执行本脚本才会注册登录后自启；已有同名任务时拒绝覆盖。
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgeTaskName = 'GoofishFeishuBridge'
if (Get-ScheduledTask -TaskName $bridgeTaskName -ErrorAction SilentlyContinue) {
    throw '同名计划任务已存在，未覆盖。请在任务计划程序中核对已有配置。'
}
$bridgeIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$bridgeScript = Join-Path $PSScriptRoot 'start-bridge.ps1'
$bridgeArguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $bridgeScript + '"'
$bridgeAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $bridgeArguments -WorkingDirectory $bridgeRoot
$bridgeTrigger = New-ScheduledTaskTrigger -AtLogOn -User $bridgeIdentity
$bridgePrincipal = New-ScheduledTaskPrincipal -UserId $bridgeIdentity -LogonType Interactive -RunLevel Limited
$bridgeSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $bridgeTaskName -Action $bridgeAction -Trigger $bridgeTrigger -Principal $bridgePrincipal -Settings $bridgeSettings -Description '本机三个闲鱼账号与本人飞书私聊的消息桥'
