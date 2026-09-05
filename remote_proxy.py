# -*- coding: utf-8 -*-
"""把密码代理作为一个独立进程运行。用法: python remote_proxy.py [port]"""
import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
sys.stdout.reconfigure(encoding="utf-8")
import remote_access

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8095
    backend = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:8090"
    srv = remote_access.start_proxy(port, backend=backend)
    print(f"密码代理已启动: 127.0.0.1:{port} -> {backend}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
        srv.server_close()
