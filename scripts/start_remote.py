# -*- coding: utf-8 -*-
"""
start_remote.py — 一键启动远程访问链（平台 + 密码代理）
  公网隧道由页面顶部"远程访问"菜单 / /api/remote/tunnel 统一开关（进程级，跨进程一致）。
  避免双起点冲突：本脚本不 start 隧道。
  1. 平台 web_server.py (8090, 仅本机)
  2. 密码代理 remote_proxy.py (8095 -> 8090)
用法: python scripts/start_remote.py   （或双击 start_remote.cmd）
"""
import sys, os, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import remote_access

def _path(p):
    return os.path.join(BASE, p)

def main():
    procs = []
    # 1) 平台
    print("[1/2] 启动平台 web_server.py :8090 ...", flush=True)
    procs.append(subprocess.Popen(["python", _path("web_server.py"), "8090"], cwd=BASE))
    time.sleep(4)
    # 2) 密码代理
    print("[2/2] 启动密码代理 remote_proxy.py :8095 -> 8090 ...", flush=True)
    procs.append(subprocess.Popen(["python", _path("remote_proxy.py"), "8095", "127.0.0.1:8090"], cwd=BASE))
    time.sleep(3)
    # 服务已就绪；隧道交给页面菜单/API 一键开关
    print("✅ 平台 + 密码代理已启动（本机 http://127.0.0.1:8090）", flush=True)
    print("   公网隧道默认未开。请到页面顶部「远程访问」菜单点「开启」生成公网+二维码。", flush=True)
    print("   默认密码 admin（建议在菜单里改）。", flush=True)
    print("   Ctrl+C 停止全部。", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止全部...")
        for p in procs:
            try: p.terminate()
            except Exception: pass
        # 顺便关掉公网隧道（若在跑）
        remote_access._tunnel.stop()

if __name__ == "__main__":
    main()
