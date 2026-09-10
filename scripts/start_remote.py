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
try:                                   # 可能被 pythonw(无控制台)调用：stdout 可能为 None
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import remote_access

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW：子进程不弹控制台窗口


def _path(p):
    return os.path.join(BASE, p)


def _port_listening(port):
    """端口是否已有服务在听（避免重复启动导致闪退）。"""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def main():
    procs = []
    # 1) 平台（若 8090 已在跑——例如后台常驻实例——则跳过，避免端口冲突闪退）
    if _port_listening(8090):
        print("[1/2] 平台 8090 已在运行，跳过启动。", flush=True)
    else:
        print("[1/2] 启动平台 web_server.py :8090 ...", flush=True)
        procs.append(subprocess.Popen(["pythonw", _path("web_server.py"), "8090"],
                                      cwd=BASE, creationflags=_NO_WINDOW))
        time.sleep(4)
    # 2) 密码代理（公网入口的密码层）
    if _port_listening(8095):
        print("[2/2] 密码代理 8095 已在运行，跳过启动。", flush=True)
    else:
        print("[2/2] 启动密码代理 remote_proxy.py :8095 -> 8090 ...", flush=True)
        procs.append(subprocess.Popen(["pythonw", _path("remote_proxy.py"), "8095", "127.0.0.1:8090"],
                                      cwd=BASE, creationflags=_NO_WINDOW))
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
