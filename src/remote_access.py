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

# 子进程不弹窗：平台可能以 pythonw(无控制台) 常驻后台，若被调用的
# powershell/cloudflared/tasklist/taskkill 不指定 CREATE_NO_WINDOW，
# Windows 会给它们新建控制台窗口并闪现（打开网页探测远程状态时尤其明显）。
_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW


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
        subprocess.run(["cloudflared", "--version"], capture_output=True, timeout=5,
                       creationflags=_NO_WINDOW)
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
    """合并写入 state(保留既有字段，如 pid/url 互不覆盖)。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    st = _load_state()
    st.update(d)
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


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
def _ensure_proxy(port):
    """确保密码代理在 <port> 监听；未在跑则无窗口拉起 remote_proxy.py 并等它就绪。
    开启公网隧道前必须先有代理——否则公网流量转到代理端口无人应答，页面打不开。"""
    if _proxy_alive(port):
        return True
    proxy_script = os.path.join(BASE, "remote_proxy.py")
    if not os.path.exists(proxy_script):
        return False
    # 优先用 pythonw（无窗口）；找不到则退回当前解释器
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    try:
        subprocess.Popen([pyw, proxy_script, str(port), _BACKEND],
                         cwd=BASE, creationflags=_NO_WINDOW)
    except Exception:
        return False
    for _ in range(12):          # 最多等 6 秒
        time.sleep(0.5)
        if _proxy_alive(port):
            return True
    return False


class Tunnel:
    """基于"进程级"的隧道管理：查找/启停真实运行的 cloudflared 进程。
    这样不论隧道由哪个进程(start_remote / web_server / 手动)启动，都统一受控。"""

    def __init__(self):
        self.proc = None
        self.url = None
        self.phase = "off"
        self.log_path = os.path.join(BASE, "data", "tunnel.log")
        self._logf = None           # 隧道日志文件句柄（stop 时关闭，避免句柄泄漏）

    def _open_log(self):
        """打开隧道日志(追加)。cloudflared 输出直接重定向到该文件——
        比 PIPE 更稳（无缓冲写满/阻塞风险），且日志即时落盘便于排查公网问题。"""
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > 1024 * 1024:
                os.replace(self.log_path, self.log_path + ".1")
        except Exception:
            pass
        f = open(self.log_path, "a", encoding="utf-8", errors="replace")
        f.write(f"\n--- cloudflared started {dt.datetime.now().isoformat()} ---\n")
        f.flush()
        return f

    def _close_log(self):
        """关闭日志句柄（stop / 重新 start 前调用，避免句柄泄漏）。"""
        try:
            if self._logf:
                self._logf.close()
        except Exception:
            pass
        self._logf = None

    def _read_log_tail(self, nbytes=16384):
        """读取日志尾部，用于从中提取 trycloudflare URL。"""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - nbytes))
                return f.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    def start(self, port, timeout=45):
        # 先确保密码代理在跑（公网入口的密码层），否则隧道通了页面也打不开
        if not _ensure_proxy(port):
            self.phase = "error"
            return {"ok": False, "msg": f"密码代理(:{port})启动失败，无法开启公网"}
        # 若已有自己启动的 cloudflared 进程在跑（state 记录的 pid 存活）→ 视为已开启（复用 url）
        ex, exurl = _find_tunnel_process(port)
        if ex and exurl:
            self.phase = "ready"
            self.url = exurl
            _save_state({"url": exurl, "started_at": dt.datetime.now().isoformat(), "pid": ex})
            return {"ok": True, "url": exurl, "msg": "隧道已在运行"}
        self.phase = "starting"
        bin_ = _find_cloudflared()
        if not bin_:
            self.phase = "error"
            return {"ok": False, "msg": "cloudflared 未找到"}
        # 协议固定 http2(TCP 443)：cloudflared 默认 QUIC(UDP) 在国内网络常连不上边缘
        # （日志停在 ICMP proxy、从不输出 Registered tunnel connection），
        # 表现为隧道看似在跑但公网地址打不开（手机访问得到 Cloudflare 错误页）；http2 更稳。
        # 输出重定向到日志文件（不用 PIPE）：无写满阻塞风险，且日志即时落盘便于排查。
        self._close_log()                       # 先关掉上次残留的句柄
        self._logf = self._open_log()
        self.proc = subprocess.Popen(
            [bin_, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{port}"],
            stdout=self._logf, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
        url = None
        deadline = time.time() + timeout
        while time.time() < deadline and self.proc.poll() is None:
            time.sleep(0.5)
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", self._read_log_tail())
            if m:
                url = m.group(0)
                break
        if url:
            self.phase = "ready"
            self.url = url
            # 记录自启进程 pid，stop 只精确杀自己起的隧道，避免误杀其他程序(如 DSH 远程访问)的 cloudflared
            _save_state({"url": url, "started_at": dt.datetime.now().isoformat(),
                         "pid": self.proc.pid})
            return {"ok": True, "url": url}
        # 超时；若自己启动的进程实际在跑（state pid 或命令行匹配），则复用并同步 pid 到 state
        fpid, furl = _find_tunnel_process(port)
        if furl:
            self.phase = "ready"
            self.url = furl
            if fpid:
                _save_state({"url": furl, "started_at": dt.datetime.now().isoformat(), "pid": fpid})
            return {"ok": True, "url": furl}
        self.phase = "error"
        tail = self._read_log_tail(2048).strip().splitlines()
        return {"ok": False, "msg": "tunnel 启动超时: " + (tail[-1] if tail else "")}

    def stop(self):
        # 只停 state 记录的本平台 cloudflared；其他程序(如 DSH)的隧道绝不动
        killed = _kill_tunnel_processes()
        self._close_log()               # 进程已停，关闭日志句柄
        self.proc = None
        self.url = None
        self.phase = "off"
        return {"ok": True, "killed": killed}


_tunnel = Tunnel()


def _find_tunnel_process(port):
    """返回 (pid, url) —— 仅当 state 记录的自启 pid 仍在运行且确为 cloudflared 进程。
    关键安全点：其他程序的 cloudflared（如 DSH 远程访问隧道）不在本平台 state 中，
    绝不会被认领、误判为"本平台隧道在跑"或被误杀。port 保留作接口兼容/日志用。"""
    st = _load_state()
    pid = st.get("pid")
    if pid and _is_cloudflared_running(pid):
        return pid, st.get("url")
    # 兜底：state 无有效 pid 时（如旧版本/state 被删），按命令行匹配本平台隧道(指向代理端口)
    # 而不是认领任意 cloudflared —— 依旧不会误认 DSH 等其它程序的隧道
    pid2 = _find_owned_cloudflared_pid(port)
    if pid2:
        return pid2, st.get("url")
    return None, None


def _is_cloudflared_running(pid):
    """指定 pid 是否仍是存活的 cloudflared 进程。"""
    return str(pid) in _list_cloudflared_pids()


def _find_owned_cloudflared_pid(port):
    """用 PowerShell 查 cloudflared 进程命令行，返回指向 <port> 的 PID(若有)。
    纯 tasklist 拿不到命令行；PowerShell 是 Windows 自带。
    匹配 `--url ...<port>`（兼容 localhost/127.0.0.1/带不带 scheme 等写法）；
    端口是唯一判据——绝不匹配其他端口(如 DSH 的隧道)。全程无窗口。"""
    import subprocess as sp
    try:
        out = sp.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
             f"Where-Object {{ $_.CommandLine -match '--url .*:{port}(/|\\s|$)' }} | "
             f"Select-Object -ExpandProperty ProcessId"],
            text=True, timeout=10, errors="replace",
            creationflags=_NO_WINDOW)
    except Exception:
        return None
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.isdigit():
            return ln
    return None


def _list_cloudflared_pids():
    """列出所有 cloudflared 进程 PID(tasklist CSV 解析, 纯标准库)。无窗口。"""
    import subprocess as sp
    try:
        out = sp.check_output(["tasklist", "/fo", "csv", "/fi", "IMAGENAME eq cloudflared.exe"],
                              text=True, timeout=8, errors="replace",
                              creationflags=_NO_WINDOW)
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


def _kill_tunnel_processes():
    """只杀本平台自启的 cloudflared，返回杀掉的进程数。
    优先级：state 精确 PID → PowerShell 按命令行(指向代理端口)。
    两者都失败时返回 0（宁可停不掉也绝不误杀——旧 pid 可能已被系统复用给别的进程）。"""
    import subprocess as sp
    st = _load_state()
    targets = []
    pid = st.get("pid")
    if pid and _is_cloudflared_running(pid):
        targets.append(str(pid))
    else:
        pid2 = _find_owned_cloudflared_pid(8095)
        if pid2:
            targets.append(pid2)
    if not targets:
        return 0
    try:
        sp.run(["taskkill", "/PID", targets[0], "/F"], capture_output=True, timeout=8,
               creationflags=_NO_WINDOW)
        return 1
    except Exception:
        return 0


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
        # 只回传"是否找到 cloudflared"，不回本地路径（避免向任何访问者泄露本机路径）
        "cloudflared_found": _find_cloudflared() is not None,
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
