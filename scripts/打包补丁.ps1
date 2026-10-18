# 增量补充包生成（第五期：A+B 更新方案里的 B 部分）
#
# 用途：日常小更新只发**变更文件**，解压覆盖到程序目录即可。
#   完整包约 1.2GB（Electron + Python sidecar），补丁包通常只有几 MB。
#
# 前置：先跑过 scripts\打包.ps1（补丁从发布目录取文件，保证与完整包一致）。
#
# 用法（仓库根目录）：
#   pwsh -File scripts\打包补丁.ps1                    # 相对上一个 tag
#   pwsh -File scripts\打包补丁.ps1 -From v1.4 -To HEAD
#   pwsh -File scripts\打包补丁.ps1 -DryRun            # 只看会收哪些文件
#
# 产物：发布包\补丁\CastoriceAgent-patch-<from>-to-<to>.zip
#   包内是**相对程序目录的路径**，解压到 CastoriceAgent 文件夹即可。
#
# 重要：用户数据在 %APPDATA%\CastoriceAgent，不在程序目录里，所以
# 覆盖补丁不会碰到配置/记忆/会话。见 castorice/paths.py。

param(
    [string]$From = "",
    [string]$To = "HEAD",
    [switch]$DryRun,
    [switch]$IncludeRuntime
)

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$releaseRoot = Join-Path $root "发布包"
$release = Join-Path $releaseRoot "CastoriceAgent"
$patchDir = Join-Path $releaseRoot "补丁"

function Fail($message) {
    Write-Host "错误: $message" -ForegroundColor Red
    exit 1
}

# ---- 1. 确定版本区间 ----
Push-Location $root
try {
    if (-not $From) {
        $From = (git describe --tags --abbrev=0 2>$null)
        if (-not $From) { Fail "仓库没有 tag，请用 -From 指定起始版本" }
    }
    $fromCommit = (git rev-parse "$From^{commit}" 2>$null)
    if (-not $fromCommit) { Fail "找不到版本 $From" }
    $toCommit = (git rev-parse "$To^{commit}" 2>$null)
    if (-not $toCommit) { Fail "找不到版本 $To" }
    if ($fromCommit -eq $toCommit) { Fail "$From 与 $To 之间没有提交" }

    $range = "$fromCommit..$toCommit"
    $changes = git diff --name-status $range
    if (-not $changes) { Fail "$From..$To 没有文件变更" }
    $commitCount = (git rev-list --count $range)
}
finally {
    Pop-Location
}

# ---- 2. 仓库路径 -> 发布目录路径 ----
# 映射必须与 electron-builder.json 的 extraResources 一致。
function Map-RepoPathToRelease($repoPath) {
    $p = $repoPath -replace "/", "\"
    # 前端/主进程的任何改动都可能影响打包产物，统一整体替换 asar
    if ($p -match "^webapp\\") { return @("resources\app.asar") }
    if ($p -match "^castorice\\") { return @("resources\backend\$p") }
    if ($p -eq "ai.py")            { return @("resources\backend\ai.py") }
    if ($p -eq "main.py")          { return @("resources\backend\main.py") }
    if ($p -match "^prompts\\")     { return @("resources\backend\$p") }
    if ($p -match "^Image\\")       { return @("Image\" + $p.Substring(6)) }
    if ($p -eq "example_config.yaml") { return @("resources\backend\example_config.yaml") }
    # 脚本 / 文档 / 测试不进包
    if ($p -match "^(scripts|docs|tests)\\") { return @() }
    # config.yaml 刻意排除：程序目录里那份是模板，补丁不该覆盖
    if ($p -eq "config.yaml") { return @() }
    return @()
}

$targets = New-Object System.Collections.Generic.HashSet[string]
$excluded = New-Object System.Collections.Generic.List[string]

foreach ($line in $changes) {
    if (-not $line.Trim()) { continue }
    # 格式：<status><TAB><path>；重命名是 R100<TAB>old<TAB>new
    $parts = $line -split "`t"
    if ($parts.Count -lt 2) { continue }
    $status = $parts[0]
    $repoPath = $parts[$parts.Count - 1]

    if ($status -eq "D") {
        $excluded.Add($repoPath + "（删除项需手工清理）")
        continue
    }

    $mapped = Map-RepoPathToRelease $repoPath
    if ($mapped.Count -eq 0) { $excluded.Add($repoPath); continue }
    foreach ($m in $mapped) { [void]$targets.Add($m) }
}

if ($targets.Count -eq 0) { Fail "没有需要打进补丁的发布文件" }

# ---- 3. 是否需要重发 sidecar 运行时 ----
# PyInstaller 产物几百 MB。只有后端运行逻辑改动才值得重发。
$needsRuntime = $IncludeRuntime.IsPresent
if (-not $needsRuntime) {
    foreach ($c in $changes) {
        if ($c -match "castorice/(harness|session|permissions|bridge|modes|notify|scheduler|channels|memory|tools|providers|paths|migration)" -or
            $c -match "(ai\.py|main\.py|spawn-bridge\.py)") {
            $needsRuntime = $true
            break
        }
    }
}

Write-Host "== 补丁范围: $From -> $To ==" -ForegroundColor Cyan
Write-Host "变更提交数: $commitCount" -ForegroundColor Gray
Write-Host "发布文件:" -ForegroundColor Cyan
foreach ($t in ($targets | Sort-Object)) { Write-Host "  + $t" }
if ($needsRuntime) { Write-Host "  + resources\backend-runtime\**（后端运行时）" -ForegroundColor Yellow }
if ($excluded.Count -gt 0) {
    Write-Host "排除:" -ForegroundColor DarkGray
    foreach ($e in ($excluded | Sort-Object -Unique)) { Write-Host "  - $e" -ForegroundColor DarkGray }
}

if ($DryRun) { Write-Host "`n[DryRun] 未生成文件。" -ForegroundColor Yellow; exit 0 }

# ---- 4. 组装 ----
if (-not (Test-Path $release)) {
    Fail "发布目录不存在：$release`n请先运行 scripts\打包.ps1"
}

$staging = Join-Path $patchDir "_staging"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Path $staging -Force | Out-Null

$missing = @()
foreach ($t in $targets) {
    $src = Join-Path $release $t
    $dst = Join-Path $staging $t
    if (Test-Path $src -PathType Container) {
        # 目录型目标：整棵拷
        if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
        Copy-Item (Join-Path $src "*") $dst -Recurse -Force
    }
    elseif (Test-Path $src -PathType Leaf) {
        $dstParent = Split-Path $dst -Parent
        if (-not (Test-Path $dstParent)) { New-Item -ItemType Directory -Path $dstParent -Force | Out-Null }
        Copy-Item $src $dst -Force
    }
    else { $missing += $t }
}

if ($needsRuntime) {
    $runtimeSrc = Join-Path $release "resources\backend-runtime"
    if (Test-Path $runtimeSrc) {
        $runtimeDst = Join-Path $staging "resources\backend-runtime"
        New-Item -ItemType Directory -Path $runtimeDst -Force | Out-Null
        Copy-Item (Join-Path $runtimeSrc "*") $runtimeDst -Recurse -Force
    } else { $missing += "resources\backend-runtime" }
}

$readme = @"
CastoriceAgent 增量补丁
=======================
版本区间: $From -> $To
生成时间: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

安装方法
--------
1. 完全退出 CastoriceAgent（托盘右键 -> 退出，确认任务管理器里没有
   CastoriceAgent.exe 与 python.exe 残留）；
2. 把本压缩包内所有文件解压到 CastoriceAgent 程序文件夹，覆盖同名文件；
3. 重新启动程序。

你的数据是安全的
----------------
从 v1.5 起，配置、用户档案、记忆、对话历史存放在：
    %APPDATA%\CastoriceAgent
补丁只覆盖程序文件，不会触碰这个目录。

若包内含 resources\backend-runtime\，说明后端运行时也更新了，
解压时间会稍长（体量较大）。

回滚
----
覆盖前建议把整个程序文件夹复制一份备份。
注意：新版本可能已升级数据格式，回滚后可能读不了新数据。
"@
$readme | Out-File -FilePath (Join-Path $staging "补丁说明.txt") -Encoding UTF8

# ---- 5. 打包（真正的 zip，不是改名的 tar）----
if (-not (Test-Path $patchDir)) { New-Item -ItemType Directory -Path $patchDir -Force | Out-Null }
$safeFrom = $From -replace "[^\w.-]", "_"
$safeTo = $To -replace "[^\w.-]", "_"
$zipPath = Join-Path $patchDir "CastoriceAgent-patch-$safeFrom-to-$safeTo.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $zipPath)

$size = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Remove-Item -Recurse -Force $staging

Write-Host ""
Write-Host "补丁包已生成: $zipPath" -ForegroundColor Green
Write-Host "大小: $size MB" -ForegroundColor Green
if ($missing.Count -gt 0) {
    Write-Host "警告: 以下目标在发布目录里不存在，已跳过：" -ForegroundColor Yellow
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Yellow }
}
