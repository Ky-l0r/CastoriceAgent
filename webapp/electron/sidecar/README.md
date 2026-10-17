# sidecar 启动脚本

`spawn-bridge.py` 是 Electron 主进程拉起的 Python 后端入口：

- dev：`python webapp/electron/sidecar/spawn-bridge.py`
- prod：`<resources>/backend/spawn-bridge.py`（electron-builder extraResources 拷贝）

stdout 契约：第一行 `{"event":"ready","port":...,"token":...}`，
失败时 `{"event":"error","message":...}` 且非零退出；其余日志全部走 stderr。
资源根由 `CASTORICE_ROOT` 环境变量指定（默认仓库根）。
