# -*- coding: utf-8 -*-
"""
remote_access.py — 主力行为学平台 远程访问（cloudflared 隧道 + 密码代理）

架构：
    [公网手机/任意设备] -> https://<random>.trycloudflare.com (cloudflared quick tunnel)
                         -> http://127.0.0.1:<proxy_port> (本机密码代理, HTTP Basic)
                         -> http://127.0.0.1:8090 (平台, 仅本机绑定)

密码代理：一个 ThreadingHTTPServer，对每个请求校验 Authorization: Basic；
          校验通过则作为 HTTP 客户端转发到 8090 并把响应回传；失败返回 401。
隧道：用 cloudflared quick tunnel 置指向 8090（或代理端口）。密码保护在平台层（代理），
      公网地址无密码时被代理拦 401。

安全：平台 web_server 只绑 127.0.0.1；公网只暴露代理端口。
"""
from __future__ import annotations
import os, sys, json, base64, re, subprocess, threading, time, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import http.client

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 密码默认存 data/remote_pass.txt，可用 set_password 改
PASS_FILE = os.path.join(BASE, "data", "remote_pass.txt")
STATE_FILE = os.path.join(BASE, "data", "remote_state.json")

_BACKEND = "127.0.0.1:8090"   # 平台真实端口
_proxy = None                 # 代理服务器对象


def _find_cloudflared():
    """找 cloudflared 二进制：仓库内 bin/ -> PATH。
    用户需自备 cloudflared：把 cloudflared.exe 放本项目 bin/ 目录，或加入系统 PATH。"""
    cands = [
        os.path.join(BASE, "bin", "cloudflared.exe"),
        os.path.join(BASE, "bin", "cloudflared"),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    try:
        subprocess.run(["cloudflared", "--version"], capture_output=True, timeout=5)
        return "cloudflared"
    except Exception:
        return None


# ---------- 密码 ----------
def get_password():
    if os.path.exists(PASS_FILE):
        return open(PASS_FILE, encoding="utf-8").read().strip()
    return "admin"  # 默认密码


def set_password(pw):
    os.makedirs(os.path.dirname(PASS_FILE), exist_ok=True)
    open(PASS_FILE, "w", encoding="utf-8").write(pw.strip())
    return "ok"


# ---------- 隧道状态持久化 ----------
def _save_state(d):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(d, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ---------- 密码代理 ----------
class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 简化 keep-alive，每请求独立连接

    def handle(self):
        """捕获异常并打日志，避免被 BaseHTTPRequestHandler 静默吞掉导致挂起。"""
        try:
            super().handle()
        except Exception as e:
            print(f"[proxy] handle error: {e}", flush=True)

    def _check_auth(self):
        """校验 HTTP Basic。返回 True 放行。"""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                user, pw = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
            except Exception:
                return False
            if pw == get_password():
                return True
        return False

    def _forward(self):
        """把当前请求转发到后端 8090，回传响应。"""
        if not self._check_auth():
            body = json.dumps({"ok": False, "msg": "unauthorized"}).encode("utf-8")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="zhu-li"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except Exception as e:
                print(f"[proxy] write 401 error: {e}", flush=True)
            return
        parsed = urlparse(self.path)
        host, port = _BACKEND.split(":")
        port = int(port)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=60)
            # 转发请求路径(保留 query)
            target = parsed.path
            if parsed.query:
                target += "?" + parsed.query
            # 透传公共请求头(去掉 hop-by-hop)
            headers = {}
            for h in ("Accept", "Accept-Encoding", "Content-Type"):
                v = self.headers.get(h)
                if v:
                    headers[h] = v
            if self.command == "POST":
                # 有 body 则读取并转发 (Content-Length 已知)
                cl = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(cl) if cl else b""
                if body:
                    headers["Content-Length"] = str(len(body))
            conn.request(self.command, target, body=body if self.command == "POST" else None,
                         headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            # 回传状态 + 关键头
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
        except Exception as e:
            body = json.dumps({"ok": False, "msg": "proxy error: " + str(e)[:80]}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except Exception as e2:
                print(f"[proxy] write 502 error: {e2}", flush=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def log_message(self, fmt, *args):
        pass


def start_proxy(port=8095, backend="127.0.0.1:8090"):
    """启动密码代理（阻塞）。端口可配。"""
    global _proxy, _BACKEND
    _BACKEND = backend
    _proxy = ThreadingHTTPServer(("127.0.0.1", port), _ProxyHandler)
    return _proxy


# ---------- cloudflared 隧道 ----------
class Tunnel:
    """基于"进程级"的隧道管理：查找/启停真实运行的 cloudflared 进程。
    这样不论隧道由哪个进程(start_remote / web_server / 手动)启动，都统一受控。"""

    def __init__(self):
        self.proc = None
        self.url = None
        self.phase = "off"

    def start(self, port, timeout=45):
        # 若已有匹配的 cloudflared 进程在跑 → 视为已开启（复用 state 里存的 url）
        ex, exurl = _find_tunnel_process(port)
        if ex and exurl:
            self.phase = "ready"
            self.url = exurl
            _save_state({"url": exurl, "started_at": dt.datetime.now().isoformat()})
            return {"ok": True, "url": exurl, "msg": "隧道已在运行"}
        self.phase = "starting"
        bin_ = _find_cloudflared()
        if not bin_:
            self.phase = "error"
            return {"ok": False, "msg": "cloudflared 未找到"}
        # 用 PIPE 读 stdout，可靠提取 trycloudflare URL
        self.proc = subprocess.Popen(
            [bin_, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=0x08000000)
        url = None
        buf = []
        deadline = time.time() + timeout
        import time as _t
        while time.time() < deadline and self.proc.poll() is None:
            line = ""
            try:
                line = self.proc.stdout.readline()
            except Exception:
                _t.sleep(0.2)
                continue
            if line:
                buf.append(line.rstrip())
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    url = m.group(0)
                    break
        if url:
            self.phase = "ready"
            self.url = url
            _save_state({"url": url, "started_at": dt.datetime.now().isoformat()})
            return {"ok": True, "url": url}
        # 超时；若进程实际在跑且 state 有 url，则复用
        _, furl = _find_tunnel_process(port)
        if furl:
            self.phase = "ready"; self.url = furl
            return {"ok": True, "url": furl}
        self.phase = "error"
        return {"ok": False, "msg": "tunnel 启动超时: " + (buf[-1] if buf else "")}

    def stop(self):
        # 停掉所有指向目标的 cloudflared 进程；保留已保存的 url(供下次 start 复用/显示)
        killed = _kill_tunnel_processes(8095)
        self.proc = None
        self.url = None
        self.phase = "off"
        return {"ok": True, "killed": killed}


_tunnel = Tunnel()


def _find_tunnel_process(port):
    """查找指向指定端口的 cloudflared 进程，返回 (存在, 可能已保存的url)。
    用 tasklist(CSV) 而非 wmic(wmic 已被微软弃用, Win11 24H2 起可能移除)。"""
    import subprocess as sp
    pids = _list_cloudflared_pids()
    if not pids:
        return None, None
    # 用命令行匹配: PowerShell/WMIC 弃用后最稳的纯标准库途径是 tasklist /v 拿不到完整命令行，
    # 因此用"进程存在 + state 里记录了我们起的隧道"作为判据；url 一律以 state 为准。
    st = _load_state()
    return pids[0], st.get("url")


def _list_cloudflared_pids():
    """列出所有 cloudflared 进程 PID(tasklist CSV 解析, 纯标准库)。"""
    import subprocess as sp
    try:
        out = sp.check_output(["tasklist", "/fo", "csv", "/fi", "IMAGENAME eq cloudflared.exe"],
                              text=True, timeout=8, errors="replace")
    except Exception:
        return []
    pids = []
    for line in out.splitlines()[1:]:          # 第一行是表头
        # CSV: "image","pid","session","sess#","mem"
        if '"cloudflared' in line:
            parts = line.split('","')
            if len(parts) >= 2:
                pid = parts[1].strip('"')
                if pid.isdigit():
                    pids.append(pid)
    return pids


def _kill_tunnel_processes(port):
    """杀掉 cloudflared 进程(用 tasklist 找 PID + taskkill)。返回杀掉的进程数。"""
    import subprocess as sp
    n = 0
    for pid in _list_cloudflared_pids():
        try:
            sp.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=8)
            n += 1
        except Exception:
            pass
    return n


def status():
    """返回远程访问状态，供 /api/remote。跨进程一致：探测真实 cloudflared 进程 + 读 state 文件 + 探测代理端口。"""
    st = _load_state()
    saved_url = st.get("url")
    # 真实隧道是否在跑（进程级）
    found, found_url = _find_tunnel_process(8095)
    proxy_ok = _proxy_alive(8095)
    if found:
        phase = "ready"
        url = found_url or saved_url  # 隧道在跑 → 返回真实公网地址
    else:
        phase = "off"
        url = ""  # 隧道没跑 → 不返回地址（前端据此显示"本机/未开"而非"公网已开"）
    return {
        "proxy_port": 8095,
        "proxy_running": proxy_ok,
        "backend": _BACKEND,
        "auth": True,
        "public_url": url,
        "tunnel_phase": phase,
        "cloudflared": _find_cloudflared(),
    }


def _proxy_alive(port):
    """探测代理端口是否存活。"""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
        return True
    except Exception:
        return False


def make_qr_png(text, box_size=6, border=2):
    """生成二维码 PNG(bytes)。文本为空返回 None。"""
    if not text:
        return None
    try:
        import qrcode
        import io
        qr = qrcode.QRCode(box_size=box_size, border=border)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"[remote] make_qr error: {e}", flush=True)
        return None
