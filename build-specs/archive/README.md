# build-specs/archive —— PyInstaller 历史 spec 存档

**这里不是构建入口，请勿直接使用。**

本项目的打包流程以脚本为准：

```powershell
pwsh -File scripts\打包.ps1          # 一键打包（前端 + 后端 + 组装）
pwsh -File scripts\打包后端.ps1      # 只打 Python sidecar
pwsh -File scripts\打包补丁.ps1      # 生成增量补丁包
```

`scripts\打包后端.ps1` 通过 **PyInstaller 命令行参数**（`-m PyInstaller --name ... --add-data ...`）
完成打包，**不读取任何 `.spec` 文件**。因此下列文件不参与构建，仅作历史参考：

| 文件 | 来历 |
| :--- | :--- |
| `CastoriceAgent.spec` | 早期（Qt 界面时期）整包打包配置 |
| `castorice-sidecar.spec` | sidecar 打包草稿，参数已迁入 `打包后端.ps1` |
| `repro.spec` ~ `repro4.spec` | v1.5 排查「打包版 sidecar 卡死 240 秒」期间的对照实验 |
| `mini2.spec` / `minitest.spec` | 最小复现工程，用于隔离依赖问题 |

保留原因：复现历史问题时可对照当时的收集参数（`collect_all` 列表、`hiddenimports`）。
如需彻底清理，直接删除本目录即可，不影响任何构建脚本。
