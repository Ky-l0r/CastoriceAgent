# 后端 sidecar 打包：为 Electron 壳生成独立运行的 Python sidecar 可执行文件。
#
# 用法（在仓库根目录下）：
#   pwsh -File scripts\打包后端.ps1
#
# 产物：dist\castorice-sidecar\castorice-sidecar.exe（onedir，含 _internal/）
# electron-builder extraResources 把该目录整体放进
# release/win-unpacked/resources/backend-runtime/，主进程直接 spawn 这个 exe，
# 用户机器上无需安装 Python（绿色便携）。
#
# 入口：webapp/electron/sidecar/spawn-bridge.py（stdout ready 握手契约）。
# 瘦身经验沿用 scripts\打包.ps1：--collect-all 只收 chromadb / onnxruntime /
# tokenizers，torch/scipy/pandas/transformers/matplotlib 等运行时不导入的大件
# 全部排除（torch_cpu.dll 一个就 291MB）。

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$entry = Join-Path $root "webapp\electron\sidecar\spawn-bridge.py"
$outdir = Join-Path $root "dist"

Write-Host "== 1/2 PyInstaller 打包 sidecar ==" -ForegroundColor Cyan
Push-Location $root
try {
    python -m PyInstaller --noconfirm --clean --name castorice-sidecar --distpath "$outdir" `
        --paths "$root" `
        --collect-all chromadb `
        --collect-all onnxruntime `
        --collect-all tokenizers `
        --hidden-import ai `
        --hidden-import castorice `
        --exclude-module torch `
        --exclude-module scipy `
        --exclude-module pandas `
        --exclude-module transformers `
        --exclude-module matplotlib `
        --exclude-module tkinter `
        --exclude-module PyQt5 `
        --exclude-module PyQt6 `
        --exclude-module PySide6 `
        --exclude-module IPython `
        --exclude-module pytest `
        "$entry"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包 sidecar 失败" }
}
finally {
    Pop-Location
}

Write-Host "== 2/2 完成 ==" -ForegroundColor Cyan
$release = Join-Path $outdir "castorice-sidecar"
$size = [math]::Round((Get-ChildItem $release -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "产物目录: $release" -ForegroundColor Green
Write-Host "总大小  : $size MB" -ForegroundColor Green
