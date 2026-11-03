# 把工作台通过 Cloudflare Tunnel 暴露成一个公网 https 地址（Quick Tunnel，无需域名）
#
#   .\deploy\tunnel.ps1                 # 默认端口 8787
#   .\deploy\tunnel.ps1 -Port 8899
#
# 会做三件事：
#   1) 本地 8787 没有服务就自己拉起来（python serve.py）
#   2) 用 cloudflared 建一条临时隧道（*.trycloudflare.com）
#   3) 把公网地址写到 data\public-url.txt，并打印出来
#
# 注意：Quick Tunnel 地址在进程退出或重启后会变；要固定域名请用具名隧道
#       （cloudflared tunnel create + DNS 绑定），步骤见 deploy\cloudflare.md。

param(
  [int]$Port = 8787,
  [string]$Host_ = "127.0.0.1",
  [int]$TimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$cloudflared = Join-Path $root ".bin\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
  Write-Host "[*] 未找到 cloudflared，正在下载 ..."
  New-Item -ItemType Directory -Force -Path (Join-Path $root ".bin") | Out-Null
  Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
    -OutFile $cloudflared -UseBasicParsing
}

function Test-Server {
  try {
    $r = Invoke-RestMethod -Uri "http://$Host_`:$Port/api/health" -TimeoutSec 3
    return [bool]$r.ok
  } catch { return $false }
}

$started = $false
if (-not (Test-Server)) {
  Write-Host "[*] 本地 $Host_`:$Port 没有在跑，启动工作台 ..."
  $python = (Get-Command python -ErrorAction SilentlyContinue)
  if (-not $python) {
    $candidates = @(
      "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
      "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $python = $c; break } }
  }
  if (-not $python) { throw "找不到 Python，请先跑一次 run.bat" }
  $py = if ($python -is [string]) { $python } else { $python.Source }
  Start-Process -FilePath $py -ArgumentList "serve.py","--port","$Port" -WorkingDirectory $root -WindowStyle Hidden
  $started = $true
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Server) { break }
  }
  if (-not (Test-Server)) { throw "工作台启动失败，请先手动运行 run.bat 看报错" }
}
Write-Host "[*] 本地服务正常：http://$Host_`:$Port"

# 启动隧道并抓取公网地址
$logFile = Join-Path $root "data\tunnel.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null
if (Test-Path $logFile) { Remove-Item $logFile -Force }

Write-Host "[*] 正在通过 Cloudflare 建立隧道 ..."
$proc = Start-Process -FilePath $cloudflared `
  -ArgumentList "tunnel","--url","http://$Host_`:$Port","--no-autoupdate","--loglevel","info" `
  -RedirectStandardError $logFile -RedirectStandardOutput (Join-Path $root "data\tunnel.out.log") `
  -WindowStyle Hidden -PassThru

$publicUrl = $null
for ($i = 0; $i -lt $TimeoutSec; $i++) {
  Start-Sleep -Seconds 1
  if (Test-Path $logFile) {
    $match = Select-String -Path $logFile -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches |
      ForEach-Object { $_.Matches } | Select-Object -First 1
    if ($match) { $publicUrl = $match.Value; break }
  }
}

if (-not $publicUrl) {
  Write-Host "[x] 没抓到公网地址，看日志：$logFile"
  if (-not $proc.HasExited) { $proc.Kill() }
  exit 1
}

$urlFile = Join-Path $root "data\public-url.txt"
"$publicUrl" | Set-Content -Path $urlFile -Encoding utf8

Write-Host ""
Write-Host "=============================================================="
Write-Host "  公网地址： $publicUrl"
Write-Host "  本地地址： http://$Host_`:$Port"
Write-Host "  已写入：   $urlFile"
Write-Host "  PID：      $($proc.Id)   （关掉这个窗口 = 断开隧道，Ctrl+C 后重启即可换新地址）"
Write-Host "=============================================================="
Write-Host ""
if ($started) { Write-Host "（本次顺带启动了本地工作台，可另开窗口用 run.bat 管理）" }
Write-Host "保持这个窗口开启，按 Ctrl+C 断开隧道。"
Wait-Process -Id $proc.Id
