# 一键打包脚本（Electron 壳 + Python sidecar 嵌套，绿色便携）
#
# 用法（在仓库根目录下）：
#   pwsh -File scripts\打包.ps1                 # 全量打包（并行 + 复用缓存）
#   pwsh -File scripts\打包.ps1 -SkipBackend    # 只重建界面（改前端 / 改主进程）
#   pwsh -File scripts\打包.ps1 -SkipWeb        # 只重建后端 sidecar
#   pwsh -File scripts\打包.ps1 -Pack           # 组装发布目录（默认不组装）
#   pwsh -File scripts\打包.ps1 -CleanBackend   # 强制清 PyInstaller 缓存
#
# 产物：发布包\CastoriceAgent\  （整个文件夹拷给别人即可使用）
#
# 提速说明（第五期）
# -----------------
# 原来一次打包要二十分钟以上，时间几乎都花在两处，本脚本已改掉：
#
# 1. **PyInstaller 每次都 --clean**：它删掉 build 缓存、重新分析全部依赖
#    （chromadb / onnxruntime 加起来上万个文件）。现在默认复用缓存，只有
#    显式 -CleanBackend 才清。改几行 Python 时增量重打通常 1~2 分钟。
# 2. **每次都重跑 electron-builder**：它的耗时几乎全在复制 resources
#    （backend-runtime 是大头）。纯前端改动现在可以 -SkipBackend，直接复用。
#
# 另外界面前端（vite/tsc）与后端（PyInstaller）互不依赖，默认并行构建。

[CmdletBinding()]
param(
    # 只重建 Web 界面（跳过 PyInstaller 与 electron-builder 的 sidecar 复制）
    [switch]$SkipBackend,
    # 只重建后端 sidecar（跳过 pnpm build 与 electron-builder）
    [switch]$SkipWeb,
    # 重新组装 发布包\CastoriceAgent
    [switch]$Pack,
    # 清掉 PyInstaller 缓存（改了依赖 / 怀疑缓存坏了时才需要）
    [switch]$CleanBackend,
    # 串行构建（默认并行）
    [switch]$NoParallel
)

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$releaseRoot = Join-Path $root "发布包"
$release = Join-Path $releaseRoot "CastoriceAgent"
$unpacked = Join-Path $root "webapp\release\win-unpacked"
$sidecarDist = Join-Path $root "dist\castorice-sidecar"
$webapp = Join-Path $root "webapp"
$backendScript = Join-Path $PSScriptRoot "打包后端.ps1"
$totalStart = Get-Date

function Write-Step([string]$text) {
    Write-Host "== $text ==" -ForegroundColor Cyan
}

function Invoke-WebBuild {
    Write-Step "构建 Web UI + Electron 主进程"
    Push-Location $webapp
    try {
        pnpm run build
        if ($LASTEXITCODE -ne 0) { throw "webapp 构建失败" }
    }
    finally {
        Pop-Location
    }
}

Write-Host "CastoriceAgent 打包（增量 + 并行）" -ForegroundColor Green

# ---- 1/3 构建（并行 / 串行）----
if (-not $SkipBackend -and -not $SkipWeb -and -not $NoParallel) {
    Write-Step "并行：界面构建 ∥ sidecar 打包"
    $job = Start-Job -ScriptBlock {
        param($script, $clean)
        $jobArgs = @("-File", $script)
        if ($clean) { $jobArgs += "-Clean" }
        & pwsh @jobArgs 2>&1 | ForEach-Object { Write-Output $_ }
        return $LASTEXITCODE
    } -ArgumentList $backendScript, [bool]$CleanBackend

    $webError = $null
    try {
        Invoke-WebBuild
    }
    catch {
        $webError = $_
    }

    $null = Wait-Job $job
    # 最后一项是 job 里 return 的 exit code，其余是后端构建日志。
    $jobOutput = @(Receive-Job $job)
    Remove-Job $job
    $backendCode = 1
    if ($jobOutput.Count -gt 0) {
        $backendCode = [int]$jobOutput[-1]
        $jobOutput[0..($jobOutput.Count - 2)] | ForEach-Object {
            if ($_ -ne $null -and "$_" -ne "") { Write-Host "$_" }
        }
    }
    if ($webError) { throw $webError }
    if ($backendCode -ne 0) { throw "sidecar 打包失败（exit $backendCode）" }
}
else {
    if (-not $SkipBackend) {
        Write-Step "打包 Python sidecar（PyInstaller）"
        $backendArgs = @("-File", $backendScript)
        if ($CleanBackend) { $backendArgs += "-Clean" }
        & pwsh @backendArgs
        if ($LASTEXITCODE -ne 0) { throw "sidecar 打包失败" }
    }
    if (-not $SkipWeb) { Invoke-WebBuild }
}

# ---- 2/3 electron-builder ----
if ($SkipBackend -and -not (Test-Path $sidecarDist)) {
    throw "缺少后端产物 dist\castorice-sidecar，请先不带 -SkipBackend 打包一次"
}

if ($SkipWeb -and (Test-Path $unpacked)) {
    Write-Host "== 跳过 electron-builder（复用 webapp\release\win-unpacked）==" -ForegroundColor DarkYellow
}
else {
    Write-Step "electron-builder 嵌套打包（sidecar 为 extraResources）"
    $eb = Join-Path $webapp "node_modules\.bin\electron-builder.cmd"
    Push-Location $webapp
    try {
        $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
        # electron-builder 内部会经 PowerShell 调 pnpm list 收集依赖树；PATH 上
        # 若有拦截 stdout 的 pnpm shim（如开发工具注入的 runtime-commands），
        # JSON 输出被污染会报 "No JSON content found"。这里把系统 node 目录
        # 提到 PATH 最前，保证它解析到真实 pnpm。
        $sysNode = "C:\Program Files\node-v24.19.0-win-x64\node-v24.19.0-win-x64"
        if (Test-Path $sysNode) {
            $env:PATH = "$sysNode;$env:PATH"
        }
        & $eb --win dir
        if ($LASTEXITCODE -ne 0) { throw "electron-builder 打包失败" }
    }
    finally {
        Remove-Item Env:ELECTRON_MIRROR -ErrorAction SilentlyContinue
        Pop-Location
    }
}

$elapsed = [math]::Round(((Get-Date) - $totalStart).TotalSeconds, 1)
Write-Host "构建完成，用时 $elapsed 秒" -ForegroundColor Green

# ---- 3/3 组装发布目录（可选）----
if (-not $Pack) {
    Write-Host "未组装发布目录（需要时加 -Pack）。中间产物：$unpacked" -ForegroundColor Yellow
    if (Test-Path (Join-Path $unpacked "resources\backend-runtime")) {
        Write-Host "可直接从该目录启动 CastoriceAgent.exe 验证。" -ForegroundColor Yellow
    }
    return
}

Write-Step "组装发布目录"
if (Test-Path $release) { Remove-Item -Recurse -Force $release }
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
Copy-Item $unpacked $release -Recurse -Force

# config.yaml 不随仓库进包（extraResources 拷的是开发机配置）：
# 用示例配置覆盖，保证发布包不含真实 API Key。
# 这份只是**模板**：首次启动时 sidecar 会把它复制到数据目录
#（%APPDATA%\CastoriceAgent），之后程序只读数据目录那份。
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "resources\backend\config.yaml") -Force
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "resources\backend\example_config.yaml") -Force

# 打包前清理：用户数据（含 API Key / 记忆 / 会话）绝不能进发布包。
$leakTargets = @(
    "resources\backend\profile.json",
    "resources\backend\ui_settings.json",
    "resources\backend\database",
    "resources\backend\sessions",
    "resources\backend\channels",
    "resources\backend\logs"
)
foreach ($rel in $leakTargets) {
    $victim = Join-Path $release $rel
    if (Test-Path $victim) {
        Remove-Item $victim -Recurse -Force
        Write-Host "  已移除用户数据: $rel" -ForegroundColor DarkYellow
    }
}

# 使用说明
$manual = Join-Path $root "docs\使用说明.txt"
if (Test-Path $manual) { Copy-Item $manual $release }

# 数据位置说明（用户第一次运行前就能看到）
$dataNote = @"
CastoriceAgent 数据存放位置
===========================
首次启动后，你的配置、用户档案、记忆、对话历史会创建在：

    %APPDATA%\CastoriceAgent

（在文件资源管理器地址栏输入 %APPDATA% 回车，然后进入 CastoriceAgent 文件夹）

为什么放在这里
--------------
程序目录可以被「覆盖更新」整个替换，而你的数据放在独立目录，
所以更新程序不会影响配置、也不会丢掉聊天记录。

备份
----
直接复制 %APPDATA%\CastoriceAgent 整个文件夹即可。
其中 config.yaml 含 API Key，请勿随意分享。

重置
----
删除 %APPDATA%\CastoriceAgent 文件夹，下次启动会重新生成默认配置
（注意：记忆与对话历史会一起丢失）。
"@
$dataNote | Out-File -FilePath (Join-Path $release "数据位置说明.txt") -Encoding UTF8

$size = [math]::Round((Get-ChildItem $release -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "发布目录: $release" -ForegroundColor Green
Write-Host "总大小  : $size MB" -ForegroundColor Green
