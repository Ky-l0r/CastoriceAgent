# 一键打包脚本（v1.4：Electron 壳 + Python sidecar 嵌套，绿色便携）
#
# 用法（在仓库根目录下）：
#   pwsh -File scripts\打包.ps1
#
# 产物：发布包\CastoriceAgent\  （整个文件夹拷给别人即可使用）
#   CastoriceAgent.exe            Electron 壳（入口）
#   resources/app.asar            Web UI 与主进程代码
#   resources/backend/            config.yaml / prompts / castorice 包 / Image
#   resources/backend-runtime/    PyInstaller 产的 Python sidecar（免装 Python）
#
# 前置：webapp 依赖已安装（cd webapp; pnpm install --registry=https://registry.npmmirror.com）
# 说明：
#   - Qt 版自 v1.4 退役（ui.py 已删除），不再产出 PySide6 界面产物。
#   - chromadb / onnxruntime 的 --collect-all 与大件 --exclude 瘦身经验
#     沿用第一期（torch_cpu.dll 一个就 291MB）。
#   - 发布包里的 config.yaml 用 example_config.yaml 的占位内容，
#     不含你自己的 API Key。

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$releaseRoot = Join-Path $root "发布包"
$release = Join-Path $releaseRoot "CastoriceAgent"
$unpacked = Join-Path $root "webapp\release\win-unpacked"

Write-Host "== 1/4 打包 Python sidecar（PyInstaller，产 _internal）==" -ForegroundColor Cyan
& pwsh -File (Join-Path $PSScriptRoot "打包后端.ps1")
if ($LASTEXITCODE -ne 0) { throw "sidecar 打包失败" }

Write-Host "== 2/4 构建 Web UI + Electron 主进程 ==" -ForegroundColor Cyan
Push-Location (Join-Path $root "webapp")
try {
    pnpm run build
    if ($LASTEXITCODE -ne 0) { throw "webapp 构建失败" }
}
finally {
    Pop-Location
}

Write-Host "== 3/4 electron-builder 嵌套打包（sidecar 为 extraResources）==" -ForegroundColor Cyan
$eb = Join-Path $root "webapp\node_modules\.bin\electron-builder.cmd"
Push-Location (Join-Path $root "webapp")
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

Write-Host "== 4/4 组装发布目录 ==" -ForegroundColor Cyan
if (Test-Path $release) { Remove-Item -Recurse -Force $release }
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
Copy-Item $unpacked $release -Recurse -Force

# config.yaml 不随仓库进包（extraResources 拷的是开发机配置）：
# 用示例配置覆盖，保证发布包不含真实 API Key。
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "resources\backend\config.yaml") -Force
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "resources\backend\example_config.yaml") -Force

# 使用说明
$manual = Join-Path $root "docs\使用说明.txt"
if (Test-Path $manual) { Copy-Item $manual $release }

$size = [math]::Round((Get-ChildItem $release -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "发布目录: $release" -ForegroundColor Green
Write-Host "总大小  : $size MB" -ForegroundColor Green
