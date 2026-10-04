# 一键打包脚本：生成可直接分发的文件夹（含 exe 与运行所需资源）
#
# 用法（在仓库根目录下）：
#   pwsh -File scripts\打包.ps1
#
# 产物：发布包\CastoriceAgent\  （整个文件夹拷给别人即可使用）
#
# 说明：
#   - 用文件夹模式而不是单文件：chromadb / onnxruntime 这类库在单文件模式下
#     每次启动都要解压到临时目录（十几秒起），且容易出错。
#   - 发布包里的 config.yaml 用 example_config.yaml 的占位内容，
#     不含你自己的 API Key，可以放心给别人。

$ErrorActionPreference = "Stop"

# 本脚本位于 scripts\ 下，仓库根目录是它的上一级
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist\CastoriceAgent"
$releaseRoot = Join-Path $root "发布包"
$release = Join-Path $releaseRoot "CastoriceAgent"

Write-Host "== 0/4 准备 exe 图标 ==" -ForegroundColor Cyan
$icon = Join-Path $root "Image\CastoriceAgent.ico"
if (-not (Test-Path $icon)) {
    $avatar = Join-Path $root "Image\CastoriceAvatar.jpeg"
    if (-not (Test-Path $avatar)) { throw "缺少图标源文件: $avatar" }
    Write-Host "未找到 $icon，正在根据头像生成..." -ForegroundColor Yellow
    python -c "import sys; from PIL import Image; Image.open(sys.argv[1]).convert('RGBA').save(sys.argv[2], format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" $avatar $icon
    if ($LASTEXITCODE -ne 0) { throw "生成 ico 失败（需要 Pillow：pip install pillow）" }
}
Write-Host "图标: $icon" -ForegroundColor Green

Write-Host "== 1/4 调用 PyInstaller 打包 ==" -ForegroundColor Cyan
Push-Location $root
try {
    # 排除项说明：
    #   torch / scipy / pandas / transformers —— chromadb 声明里没有、
    #   运行时也不会导入，但 --collect-all 会把它们连带收进来，
    #   其中 torch_cpu.dll 一个文件就 291MB，排除后可省下约 460MB。
    #   onnxruntime / tokenizers 是记忆检索真正要用的，必须保留。
    python -m PyInstaller --noconfirm --clean --windowed --name CastoriceAgent --icon "$icon" `
        --collect-all chromadb `
        --collect-all onnxruntime `
        --collect-all tokenizers `
        --exclude-module torch `
        --exclude-module scipy `
        --exclude-module pandas `
        --exclude-module transformers `
        --exclude-module matplotlib `
        --exclude-module tkinter `
        --exclude-module PyQt5 `
        --exclude-module PyQt6 `
        --exclude-module IPython `
        --exclude-module pytest `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }
}
finally {
    Pop-Location
}

Write-Host "== 2/4 准备发布目录 ==" -ForegroundColor Cyan
if (Test-Path $release) { Remove-Item -Recurse -Force $release }
New-Item -ItemType Directory -Path $release -Force | Out-Null

Write-Host "== 3/4 复制程序与资源 ==" -ForegroundColor Cyan
Copy-Item (Join-Path $dist "*") $release -Recurse -Force

# 运行期需要的资源（放在 exe 旁边，方便用户自行修改）
Copy-Item (Join-Path $root "Image")   $release -Recurse -Force
Copy-Item (Join-Path $root "prompts") $release -Recurse -Force

# 配置：使用示例配置，不含真实 API Key
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "config.yaml")
Copy-Item (Join-Path $root "example_config.yaml") (Join-Path $release "example_config.yaml")

# 使用说明（放在 docs\ 下，复制进发布包根目录给用户看）
$manual = Join-Path $root "docs\使用说明.txt"
if (Test-Path $manual) { Copy-Item $manual $release }

# 不放 database / logs / ui_settings.json：让用户以全新状态启动

Write-Host "== 4/4 完成 ==" -ForegroundColor Cyan
$size = [math]::Round((Get-ChildItem $release -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "发布目录: $release" -ForegroundColor Green
Write-Host "总大小  : $size MB" -ForegroundColor Green
Write-Host ""
Write-Host "目录内容:" -ForegroundColor Yellow
Get-ChildItem $release | Select-Object Mode, Name | Format-Table -AutoSize
