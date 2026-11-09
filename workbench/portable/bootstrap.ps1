#requires -version 5.1
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Repair,
    [switch]$OfflineOnly,
    [switch]$NoBrowser,
    [switch]$TestMode,
    [int]$Port = 0
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONHOME = ''
$env:PYTHONPATH = ''
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$packageRoot = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $packageRoot
$manifestPath = Join-Path $packageRoot 'package-manifest.json'
$runtimeRoot = Join-Path $packageRoot 'runtime'
$pythonExe = Join-Path $runtimeRoot 'python.exe'
$siteRoot = Join-Path $runtimeRoot 'Lib\site-packages'
$cacheRoot = Join-Path $packageRoot 'offline-cache'
$logRoot = Join-Path $packageRoot 'logs'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$bootstrapLock = $null

function Write-Step([int]$Number, [string]$Message) {
    Write-Host ("[{0}/6] {1}" -f $Number, $Message) -ForegroundColor Cyan
    Write-Progress -Id 1 -Activity '生图工作台 · 一键启动检查' -Status $Message -PercentComplete ([Math]::Min(100, $Number * 100 / 6))
}
function Safe-Path([string]$Base, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative.Contains(':')) { throw '包内文件路径异常。' }
    $baseFull = [IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath((Join-Path $Base $Relative))
    if (-not $full.StartsWith($baseFull, [StringComparison]::OrdinalIgnoreCase)) { throw '文件路径不能越出解压目录。' }
    return $full
}
function Hash-Matches([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    return ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Expected.ToLowerInvariant())
}
function Test-FileMap($Map, [string]$Base) {
    foreach ($property in $Map.PSObject.Properties) {
        $file = Safe-Path $Base $property.Name
        if (-not (Hash-Matches $file ([string]$property.Value))) { return $false }
    }
    return $true
}
function Download-Verified($Artifact, [string]$Target) {
    if ($OfflineOnly) { throw ('离线缓存缺失：' + [IO.Path]::GetFileName($Target)) }
    $allowed = @('repo.huaweicloud.com', 'mirrors.huaweicloud.com', 'mirrors.aliyun.com', 'pypi.tuna.tsinghua.edu.cn')
    foreach ($address in $Artifact.urls) {
        $uri = [Uri]$address
        if ($uri.Scheme -ne 'https' -or $allowed -notcontains $uri.Host) { throw '下载地址不在国内HTTPS源列表。' }
        $temporary = $Target + '.partial'
        $response = $null; $inputStream = $null; $outputStream = $null
        try {
            Write-Host ('正在从国内源下载：' + [IO.Path]::GetFileName($Target))
            $request = [Net.HttpWebRequest]::Create($uri)
            $request.Timeout = 30000; $request.ReadWriteTimeout = 120000
            $request.UserAgent = 'InternalWorkbench-9.4.1'
            $response = $request.GetResponse()
            if ($allowed -notcontains $response.ResponseUri.Host) { throw '下载跳转地址不在国内源列表。' }
            $total = $response.ContentLength; $received = [long]0; $lastPrinted = -10
            $inputStream = $response.GetResponseStream()
            $outputStream = [IO.File]::Open($temporary, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $buffer = New-Object byte[] 262144
            while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $outputStream.Write($buffer, 0, $count); $received += $count
                if ($total -gt 0) {
                    $percent = [Math]::Min(100, [int]($received * 100 / $total))
                    Write-Progress -Id 2 -ParentId 1 -Activity ([IO.Path]::GetFileName($Target)) -Status ("{0}% · {1:N1}/{2:N1} MB" -f $percent, ($received / 1MB), ($total / 1MB)) -PercentComplete $percent
                    if ($percent -ge ($lastPrinted + 10)) { Write-Host ("  下载进度 {0}%" -f $percent); $lastPrinted = $percent }
                } else { Write-Progress -Id 2 -ParentId 1 -Activity ([IO.Path]::GetFileName($Target)) -Status ("已下载 {0:N1} MB" -f ($received / 1MB)) }
            }
            $outputStream.Dispose(); $outputStream = $null
            $inputStream.Dispose(); $inputStream = $null
            $response.Dispose(); $response = $null
            if (-not (Hash-Matches $temporary $Artifact.sha256)) { throw '下载校验失败。' }
            Move-Item -LiteralPath $temporary -Destination $Target -Force
            Write-Progress -Id 2 -Completed -Activity '下载完成'
            return
        } catch {
            Write-Host '  当前国内源未成功，正在尝试下一个国内源。' -ForegroundColor Yellow
        } finally {
            if ($outputStream) { $outputStream.Dispose() }
            if ($inputStream) { $inputStream.Dispose() }
            if ($response) { $response.Dispose() }
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
    }
    throw ('国内源下载失败，请检查网络后再次双击：' + [IO.Path]::GetFileName($Target))
}
function Ensure-Artifact($Artifact) {
    $target = Safe-Path $cacheRoot $Artifact.filename
    if (-not (Hash-Matches $target $Artifact.sha256)) {
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
        Download-Verified $Artifact $target
    }
    return $target
}
function Expand-SafeZip([string]$ZipPath, [string]$Destination, [switch]$Wheel) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $archive.Entries) {
            if (-not $entry.Name) { continue }
            $relative = $entry.FullName.Replace('/', '\')
            if ($Wheel -and $relative -match '^[^\\]+\.data\\(purelib|platlib)\\(.+)$') { $relative = $Matches[2] }
            elseif ($Wheel -and $relative -match '^[^\\]+\.data\\') { continue }
            $target = Safe-Path $Destination $relative
            New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
            $inputStream = $entry.Open(); $outputStream = $null
            try {
                $outputStream = [IO.File]::Open($target, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $inputStream.CopyTo($outputStream)
            } finally { if ($outputStream) { $outputStream.Dispose() }; $inputStream.Dispose() }
        }
    } finally { $archive.Dispose() }
}
function Assert-NotRunning {
    foreach ($dir in @('workbench\data', 'workbench\data-portable-test')) {
        $statePath = Join-Path $packageRoot ($dir + '\portable-state.json')
        if (Test-Path -LiteralPath $statePath) {
            try {
                $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $running = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
                if ($running -and $running.Path -and [IO.Path]::GetFullPath($running.Path).Equals($pythonExe, [StringComparison]::OrdinalIgnoreCase)) { throw '修复前请先关闭该目录正在运行的工作台窗口，再双击启动。' }
            } catch { if ($_.Exception.Message -like '修复前*') { throw } }
        }
    }
}
try {
    Write-Host ''
    Write-Host '生图工作台 · 内部便携版 9.4.1' -ForegroundColor White
    Write-Host '首次自动检查并配置；正常启动使用包内环境，不要求预装 Python。'
    Write-Step 1 '检查 Windows、64位架构、目录权限和磁盘空间'
    if (-not [Environment]::Is64BitOperatingSystem -or ([Environment]::OSVersion.Version.Major -lt 10)) { throw '此包支持 Windows 10/11 64位，请使用对应系统电脑。' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw '此包为 Intel/AMD x64版，ARM电脑需要另行提供对应运行包。' }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw '请先完整解压整个ZIP，再双击解压目录中的一键启动.bat。' }
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $probe = Join-Path $packageRoot ('.write-' + [Guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($probe, 'ok', $utf8); Remove-Item -LiteralPath $probe -Force
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($packageRoot))
    if ($drive.AvailableFreeSpace -lt 500MB) { throw '解压所在磁盘可用空间不足500MB，请移到空间充足的磁盘后启动。' }
    $bootstrapLock = [IO.File]::Open((Join-Path $logRoot 'bootstrap.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Step 2 '检查网页、Three.js、款式库与内部API配置文件'
    $applicationOK = Test-FileMap $manifest.application_files $packageRoot
    $privateOK = (Test-Path -LiteralPath (Join-Path $packageRoot 'workbench\.env') -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $packageRoot 'workbench\config.json') -PathType Leaf)
    if (-not $applicationOK -or -not $privateOK) {
        Assert-NotRunning
        Write-Host '发现缺失或损坏的项目文件，正在从包内缓存修复…' -ForegroundColor Yellow
        $applicationZip = Ensure-Artifact $manifest.application_archive
        $temporaryBase = if ($env:PI_SCRATCH_DIR) { $env:PI_SCRATCH_DIR } else { [IO.Path]::GetTempPath() }
        $restore = Join-Path $temporaryBase ('wb-restore-' + [Guid]::NewGuid().ToString('N').Substring(0,12))
        try {
            Expand-SafeZip $applicationZip $restore
            foreach ($property in $manifest.application_files.PSObject.Properties) {
                $target = Safe-Path $packageRoot $property.Name
                if (-not (Hash-Matches $target ([string]$property.Value))) {
                    $source = Safe-Path $restore $property.Name
                    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
                    Copy-Item -LiteralPath $source -Destination $target -Force
                }
            }
            foreach ($name in @('workbench\.env', 'workbench\config.json')) {
                $target = Safe-Path $packageRoot $name
                if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { Copy-Item -LiteralPath (Safe-Path $restore $name) -Destination $target }
            }
        } finally { if (Test-Path -LiteralPath $restore) { Remove-Item -LiteralPath $restore -Recurse -Force } }
        if (-not (Test-FileMap $manifest.application_files $packageRoot)) { throw '项目资源修复未完成，请重新完整解压ZIP。' }
    }
    Write-Host 'Three.js和全部网页资源已内置；无需Node.js或npm。'
    Write-Step 3 '检查自带Python及微软运行库（不改系统PATH）'
    $runtimeOK = Test-FileMap $manifest.python_files $runtimeRoot
    if ($Repair -or -not $runtimeOK) {
        Assert-NotRunning
        $archive = Ensure-Artifact $manifest.python_archive
        Write-Host '正在配置包内Python运行环境…'
        Expand-SafeZip $archive $runtimeRoot
        $pth = "python312.zip`r`n.`r`nLib\site-packages`r`n..\workbench`r`nimport site`r`n"
        [IO.File]::WriteAllText((Join-Path $runtimeRoot 'python312._pth'), $pth, $utf8)
    }
    if (-not (Test-FileMap $manifest.python_files $runtimeRoot)) { throw 'Python运行文件校验失败，请重新解压或联网修复。' }
    Write-Step 4 '检查全部依赖，缺失时优先从离线缓存自动配置'
    $dependenciesOK = Test-FileMap $manifest.dependency_files $runtimeRoot
    if ($Repair -or -not $dependenciesOK) {
        Assert-NotRunning
        $index = 0; $count = @($manifest.wheels).Count
        foreach ($wheel in $manifest.wheels) {
            $index++
            Write-Progress -Id 2 -ParentId 1 -Activity '配置Python依赖' -Status ("{0}/{1} · {2}" -f $index, $count, [IO.Path]::GetFileName($wheel.filename)) -PercentComplete ($index * 100 / [Math]::Max(1, $count))
            Write-Host ("  配置依赖 {0}/{1}：{2}" -f $index, $count, [IO.Path]::GetFileName($wheel.filename))
            $wheelPath = Ensure-Artifact $wheel
            Expand-SafeZip $wheelPath $siteRoot -Wheel
        }
        Write-Progress -Id 2 -Completed -Activity '依赖配置完成'
    }
    if (-not (Test-FileMap $manifest.dependency_files $runtimeRoot)) { throw '依赖文件校验未完成，请保留本窗口联系内部维护人员。' }
    Write-Step 5 '检查图像编解码、款式、主备接口、AI与飞书配置'
    $launcher = Join-Path $packageRoot 'workbench\portable\launch.py'
    $previousPreference = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $checkArguments = @('-B', $launcher, '--check')
    if ($TestMode) { $checkArguments += '--test-mode' }
    $reportText = & $pythonExe @checkArguments 2>&1
    $reportExit = $LASTEXITCODE; $ErrorActionPreference = $previousPreference
    if ($reportExit -ne 0) {
        $reportText | Out-File -LiteralPath (Join-Path $logRoot 'environment-check.log') -Encoding utf8
        throw '运行依赖或内部API配置检查未通过；详情见logs/environment-check.log，不会输出密钥。'
    }
    $report = ($reportText | Out-String).Trim() | ConvertFrom-Json
    if (-not $report.ok) { throw '环境检查未通过。' }
    Write-Host ("Python {0} · {1} 款 · {2} 张官方效果图 · 原生图片编解码正常" -f $report.python, $report.types, $report.effect_images)
    Write-Host ('主接口／备用接口／AI配置均已就绪；飞书配置：' + $(if ($report.feishu_configured) {'已就绪'} else {'未启用'}))
    [IO.File]::WriteAllText((Join-Path $logRoot 'environment-check.json'), ($report | ConvertTo-Json -Depth 8), $utf8)
    Write-Step 6 '准备本机独立数据与端口，自动打开工作台'
    Write-Progress -Id 1 -Completed -Activity '启动检查完成'
    $bootstrapLock.Dispose(); $bootstrapLock = $null
    if ($CheckOnly) { Write-Host '全部检查完成，退出码0。' -ForegroundColor Green; exit 0 }
    $arguments = @('-B', $launcher)
    if ($NoBrowser) { $arguments += '--no-open' }
    if ($TestMode) { $arguments += '--test-mode' }
    if ($Port -gt 0) { $arguments += @('--port', [string]$Port) }
    & $pythonExe @arguments
    exit $LASTEXITCODE
} catch {
    Write-Progress -Id 1 -Completed -Activity '检查结束'
    Write-Progress -Id 2 -Completed -Activity '检查结束'
    Write-Host ''
    Write-Host ('启动未完成：' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '请确认已完整解压，目录可写；网络修复仅使用国内HTTPS源。再次双击会继续检测。'
    exit 1
} finally {
    if ($bootstrapLock) { $bootstrapLock.Dispose() }
}
