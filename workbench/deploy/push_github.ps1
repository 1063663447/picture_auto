# 把整个仓库（picture_auto：skill + workbench）推到 GitHub，供 Render / Koyeb / Cloud Run 一键部署
#
#   .\deploy\push_github.ps1 -Repo https://github.com/账号/picture_auto.git -Token ghp_xxx
#   .\deploy\push_github.ps1 -Repo https://github.com/账号/picture_auto.git -Token ghp_xxx -Message "deploy v1"
#
# Token 用 fine-grained PAT（Contents: Read and write 即可）。只在本机 git remote 里用一次，
# 不会写进任何被提交的文件（.gitignore 已排除 .env / data / .bin）。

param(
  [Parameter(Mandatory = $true)][string]$Repo,
  [Parameter(Mandatory = $true)][string]$Token,
  [string]$Branch = "main",
  [string]$Message = "deploy: apparel recolor workbench"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "[x] 没有 git。先装：winget install --id Git.Git -e"
  exit 1
}

if (-not (Test-Path ".git")) {
  Write-Host "[*] 初始化仓库"
  git init | Out-Null
}

# 确保不会把敏感文件带上去
$ignore = @(
  "workbench/.env",
  "workbench/data/",
  "workbench/.bin/",
  "workbench/docs/",
  "workbench/.skills/",
  "__pycache__/",
  "*.pyc"
)
$gitignorePath = ".gitignore"
$existing = if (Test-Path $gitignorePath) { Get-Content $gitignorePath -Raw } else { "" }
$missing = $ignore | Where-Object { $existing -notmatch [regex]::Escape($_) }
if ($missing) {
  Add-Content -Path $gitignorePath -Value ("`n# 部署时补充`n" + ($missing -join "`n")) -Encoding UTF8
}

$authUrl = $Repo -replace "^https://", "https://x-access-token:$Token@"
git remote remove origin 2>$null
git remote add origin $authUrl

git add -A
$status = git status --porcelain
if ($status) {
  git -c user.name="workbench-bot" -c user.email="bot@local" commit -m $Message | Out-Null
} else {
  Write-Host "[*] 没有变更，直接推送现有提交"
}

Write-Host "[*] 推送到 $Branch ..."
git -c core.askpass= -c credential.helper= push -u origin "HEAD:$Branch" --force

# 推完把带 token 的 remote 换回干净地址，避免 token 留在 .git/config
git remote set-url origin $Repo
Write-Host ""
Write-Host "=============================================================="
Write-Host "  已推送： $Repo （分支 $Branch）"
Write-Host "  下一步："
Write-Host "   Render → New → Blueprint → 选这个仓库 → Apply（deploy/render.yaml 已就绪）"
Write-Host "   然后在 Render 的 Environment 填 TERRA_API_KEY / DEEPSEEK_API_KEY"
Write-Host "=============================================================="
