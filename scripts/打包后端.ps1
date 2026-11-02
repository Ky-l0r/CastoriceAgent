# 后端 sidecar 打包：为 Electron 壳生成独立运行的 Python sidecar 可执行文件。
#
# 用法（在仓库根目录下）：
#   pwsh -File scripts\打包后端.ps1            # 增量（复用 PyInstaller 缓存，默认）
#   pwsh -File scripts\打包后端.ps1 -Clean     # 清缓存全量重打（改依赖时用）
#
# 产物：dist\castorice-sidecar\castorice-sidecar.exe（onedir，含 _internal/）
#
# 关于 -Clean（第五期提速）
# -----------------------
# 原来固定带 --noconfirm --clean：每次都会删掉 build/ 缓存，重新分析
# chromadb / onnxruntime / tokenizers 的上万个文件，只改了几行 Python 也要
# 十几分钟。现在默认**不清缓存**——PyInstaller 会复用上次的分析结果，只
# 重新收集变更的模块，通常 1~2 分钟。改了依赖版本、或怀疑缓存坏了时才用
# -Clean（它会先删 build/castorice-sidecar 再打包）。
#
# 另一处提速：--collect-all 只保留运行期真正会加载的三项；torch / scipy /
# pandas / transformers / matplotlib 等大件全部排除（torch_cpu.dll 一个就
# 291MB），这一步直接影响最终的收集与复制耗时。

[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$entry = Join-Path $root "webapp\electron\sidecar\spawn-bridge.py"
$outdir = Join-Path $root "dist"
$workdir = Join-Path $root "build"

if ($Clean) {
    Write-Host "== 清理 PyInstaller 缓存 ==" -ForegroundColor DarkYellow
    foreach ($victim in @((Join-Path $workdir "castorice-sidecar"), (Join-Path $outdir "castorice-sidecar"))) {
        if (Test-Path $victim) {
            Remove-Item $victim -Recurse -Force
            Write-Host "  已删除 $victim" -ForegroundColor DarkYellow
        }
    }
}

$modeLabel = if ($Clean) { "全量" } else { "增量（复用缓存）" }
Write-Host "== PyInstaller 打包 sidecar：$modeLabel ==" -ForegroundColor Cyan
Push-Location $root
try {
    $pyiArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--name", "castorice-sidecar",
        "--distpath", "$outdir",
        "--workpath", "$workdir",
        "--paths", "$root",
        "--collect-all", "chromadb",
        "--collect-all", "onnxruntime",
        "--collect-all", "tokenizers",
        "--hidden-import", "ai",
        "--hidden-import", "castorice",
        "--exclude-module", "torch",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "transformers",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide6",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        "$entry"
    )
    python @pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包 sidecar 失败" }
}
finally {
    Pop-Location
}

Write-Host "== 完成 ==" -ForegroundColor Cyan
$release = Join-Path $outdir "castorice-sidecar"
$size = [math]::Round((Get-ChildItem $release -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "产物目录: $release" -ForegroundColor Green
Write-Host "总大小  : $size MB" -ForegroundColor Green
