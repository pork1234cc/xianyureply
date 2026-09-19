# 显式执行本脚本才会注册登录后自启；已有同名任务时拒绝覆盖。
param([string]$BitBrowserPath = '')
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgeTaskName = 'GoofishFeishuBridge'
if (Get-ScheduledTask -TaskName $bridgeTaskName -ErrorAction SilentlyContinue) {
    throw '同名计划任务已存在，未覆盖。请在任务计划程序中核对已有配置。'
}
$bridgeIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$bridgeScript = Join-Path $PSScriptRoot 'start-bridge.ps1'
$bridgeArguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $bridgeScript + '"'
if (-not $BitBrowserPath) {
    $BitBrowserPath = Get-Process | Where-Object { $_.ProcessName -eq '比特浏览器' } | Select-Object -First 1 -ExpandProperty Path
}
if ($BitBrowserPath) {
    if (-not (Test-Path -LiteralPath $BitBrowserPath -PathType Leaf)) { throw '比特浏览器程序路径不存在。' }
    $bridgeArguments += ' -BitBrowserPath "' + $BitBrowserPath + '"'
}
$bridgeAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $bridgeArguments -WorkingDirectory $bridgeRoot
$bridgeTrigger = New-ScheduledTaskTrigger -AtLogOn -User $bridgeIdentity
$bridgePrincipal = New-ScheduledTaskPrincipal -UserId $bridgeIdentity -LogonType Interactive -RunLevel Limited
$bridgeSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskName $bridgeTaskName -Action $bridgeAction -Trigger $bridgeTrigger -Principal $bridgePrincipal -Settings $bridgeSettings -Description '本机三个闲鱼账号与本人飞书私聊的消息桥' -ErrorAction Stop
} catch {
    if ($_.Exception.HResult -ne -2147024891 -and $_.FullyQualifiedErrorId -notmatch '80070005') { throw }
    # 普通用户不能注册计划任务时，使用该用户自己的登录启动目录。
    $bridgeShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'GoofishFeishuBridge.lnk'
    if (Test-Path -LiteralPath $bridgeShortcutPath) { throw '登录启动快捷方式已存在，未覆盖。' }
    $bridgeShell = New-Object -ComObject WScript.Shell
    $bridgeShortcut = $bridgeShell.CreateShortcut($bridgeShortcutPath)
    $bridgeShortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
    $bridgeShortcut.Arguments = $bridgeArguments
    $bridgeShortcut.WorkingDirectory = $bridgeRoot
    $bridgeShortcut.WindowStyle = 7
    $bridgeShortcut.Description = '闲鱼消息桥：Windows 登录后后台启动'
    $bridgeShortcut.Save()
    Write-Output ('已配置当前用户登录启动：' + $bridgeShortcutPath)
}
