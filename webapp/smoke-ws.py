"""端到端 WS smoke：复刻主进程 BridgeClient 的握手 + RPC + 状态查询流程。"""
import asyncio
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve().parent / "electron" / "sidecar" / "spawn-bridge.py"


async def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready.get("event") == "ready", ready
        print("READY:", ready)

        import websockets

        async with websockets.connect(
            f"ws://127.0.0.1:{ready['port']}", max_size=8 * 1024 * 1024
        ) as ws:
            await ws.send(json.dumps({"type": "hello", "token": ready["token"]}))
            frame = json.loads(await ws.recv())
            assert frame["type"] == "hello_ok", frame
            print("HELLO_OK")

            await ws.send(json.dumps({"type": "rpc", "id": "r1", "method": "mode.list", "params": {}}))
            while True:
                frame = json.loads(await ws.recv())
                if frame["type"] == "rpc_result" and frame["id"] == "r1":
                    print("MODE_LIST:", json.dumps(frame["result"], ensure_ascii=False))
                    break
                if frame["type"] == "rpc_error":
                    print("RPC_ERROR:", frame)
                    return 1

            # 错误路径：错误 token 握手应被拒
            async with websockets.connect(f"ws://127.0.0.1:{ready['port']}") as bad:
                await bad.send(json.dumps({"type": "hello", "token": "WRONG"}))
                try:
                    await asyncio.wait_for(bad.recv(), timeout=5)
                    print("AUTH_NEGATIVE: FAIL (expected close)")
                    return 1
                except Exception:
                    print("AUTH_NEGATIVE: PASS (bad token disconnected)")

            print("SMOKE_PASS")
            return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
