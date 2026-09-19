# 使用本项目虚拟环境启动，不影响其他项目的 Python 和代理设置。
param([string]$BitBrowserPath = '')
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgePython = Join-Path $bridgeRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bridgePython)) { throw '项目虚拟环境不存在，请先按启动说明安装。' }
Set-Location -LiteralPath $bridgeRoot
$env:VIRTUAL_ENV = Join-Path $bridgeRoot '.venv'
$env:PATH = (Join-Path $env:VIRTUAL_ENV 'Scripts') + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
# 登录自启时先确保比特客户端和 Local API 就绪。
if ($BitBrowserPath) {
    $bridgeApi = & $bridgePython -c "from goofish_bridge.config import Config; from pathlib import Path; c=Config.load(Path('config.yaml')); b=c.raw.get('bitbrowser') or {}; print(b.get('api_url', 'http://127.0.0.1:54345') if b.get('enabled') else '')"
    if ($LASTEXITCODE -ne 0) { throw '读取比特浏览器配置失败。' }
    if ($bridgeApi) {
        if (-not (Get-Process | Where-Object { $_.Path -eq $BitBrowserPath })) {
            Start-Process -FilePath $BitBrowserPath -WindowStyle Hidden
        }
        $bridgeReady = $false
        $bridgeDeadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $bridgeDeadline) {
            try {
                $bridgeResponse = Invoke-RestMethod -Uri ($bridgeApi.TrimEnd('/') + '/group/list') -Method Post -ContentType 'application/json' -Body '{"page":0,"pageSize":1}' -TimeoutSec 5
                if ($bridgeResponse.success -eq $true) { $bridgeReady = $true; break }
            } catch {
                # 客户端启动和登录期间接口可能暂不可用，等待后重试。
            }
            Start-Sleep -Seconds 3
        }
        if (-not $bridgeReady) { throw '比特浏览器 API 三分钟内未就绪，请确认客户端已登录。' }
    }
}
& $bridgePython -m goofish_bridge run
exit $LASTEXITCODE
