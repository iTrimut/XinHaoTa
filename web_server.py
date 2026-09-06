# -*- coding: utf-8 -*-
"""
web_server.py — 主力行为学平台 可视化 Web 服务
Python 标准库 http.server + 本地 ECharts。JSON API 供前端渲染。

启动:
  python web_server.py [端口]    默认 8090
接口:
  GET  /                     看板页
  GET  /api/stocks           全部股票三态结论 + 市场环境(按 brain 开关)
  GET  /api/kline/<code>     某只股票K线+均线+信号+买卖点
  GET  /api/brain            技能目录(大脑选择面板)
  POST /api/brain            保存技能开关
  GET  /api/watchlist        当前自选股
  POST /api/watchlist/add    增           body: {"code","name","sector"}
  POST /api/watchlist/del    删           body: {"code"}
"""
from __future__ import annotations
import os, sys, json, re, time, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Windows 控制台默认 GBK → 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "src"))
import data_fetch, signal_engine, context_engine, brain, store, datasource, refresh as refresh_mod
import threading
import industry as industry_mod
import remote_access as remote_mod

WEB_DIR = os.path.join(BASE, "web")
HOST = "127.0.0.1"

# 允许被静态服务的文件白名单 + 后缀白名单（杜绝任意文件读取）
_SAFE_STATIC = {"index.html", "echarts.min.js"}
_SAFE_SUFFIX = (".js", ".css", ".png", ".svg", ".ico")

_cache = {"analyses": None, "market": None, "ts": None, "sector_map": None}


def _enabled():
    return store.get_brain()


def _control_bits(brain_map):
    """把 brain 开关映射成 signal_engine/context_engine 需要的控制位。"""
    return {
        "state": brain_map.get("ma5", True),
        "mode": brain_map.get("dual_mode", True),
        "spacetime": brain_map.get("spacetime", True),
        "exclude": brain_map.get("exclude", True),
        "leader": brain_map.get("leader", True),
        "rotation": brain_map.get("rotation", True),
        "whale": brain_map.get("whale", True),
        "quarter": brain_map.get("quarter", True),
    }


def _analyze_all(force=False):
    """全量分析（带30分钟进程缓存）。brain 改动时 force 强制重建。"""
    now = dt.datetime.now()
    if not force and _cache["analyses"] is not None and _cache["ts"] and (now - _cache["ts"]).seconds < 1800:
        return _cache["analyses"], _cache["market"]
    controls = _control_bits(_enabled())
    watch = store.get_watchlist()
    sector_map = {s["code"]: s["sector"] for s in watch}
    analyses = []
    for s in watch:
        try:
            rows = data_fetch.fetch_kline(s["code"], limit=300, fast=True)
            if not rows:
                # 抓不到数据 → 仍保留该票，标记 status=error，绝不静默丢弃（避免"添加没反应"）
                analyses.append({
                    "code": s["code"], "name": s["name"] or s["code"], "sector": s["sector"],
                    "status": "error", "error": "数据暂不可用(行情源限流)，稍后重试",
                    "_rows": [],
                    "ma5": None, "ma10": None, "ma20": None,
                    "last_date": "", "last_close": None, "last_pct": None, "last_turnover": None,
                    "state": "数据待获取", "state_reason": "", "mode": "", "mode_reason": "",
                    "space_ok": False, "time_ok": False, "space_detail": "",
                    "flags": [], "stop": {}, "recency_note": "", "enabled": controls,
                    "verdict": {"action": "watch", "label": "数据获取中", "reason": "行情源暂不可用，稍后自动重试。"},
                })
                continue
            a = signal_engine.analyze(rows, name=s["name"] or s["code"], code=s["code"], enabled=controls)
            a["sector"] = s["sector"]
            a["_rows"] = rows
            a["status"] = "ok"
            a["error"] = ""
            analyses.append(a)
        except Exception as e:
            print(f"  ⚠️ {s['code']} 分析失败: {str(e)[:50]}")
            analyses.append({
                "code": s["code"], "name": s["name"] or s["code"], "sector": s["sector"],
                "status": "error", "error": f"分析失败: {str(e)[:45]}", "_rows": [],
                "ma5": None, "ma10": None, "ma20": None,
                "last_date": "", "last_close": None, "last_pct": None, "last_turnover": None,
                "state": "数据待获取", "state_reason": "", "mode": "", "mode_reason": "",
                "space_ok": False, "time_ok": False, "space_detail": "",
                "flags": [], "stop": {}, "recency_note": "", "enabled": controls,
                "verdict": {"action": "watch", "label": "无法分析", "reason": str(e)[:45]},
            })
    market = context_engine.analyze_context(analyses, sector_map, None, controls)
    _cache.update({"analyses": analyses, "market": market, "ts": now, "sector_map": sector_map})
    return analyses, market


def _json_serial(a):
    return {
        "code": a["code"], "name": a["name"], "sector": a.get("sector", ""),
        "status": a.get("status", "ok"), "error": a.get("error", ""),
        "last_date": a["last_date"], "last_close": a["last_close"], "last_pct": a["last_pct"],
        "last_turnover": a["last_turnover"],
        "ma5": a.get("ma5"), "ma10": a.get("ma10"), "ma20": a.get("ma20"),
        "state": a["state"], "state_reason": a["state_reason"],
        "mode": a["mode"], "mode_reason": a["mode_reason"],
        "space_ok": a["space_ok"], "time_ok": a["time_ok"], "space_detail": a["space_detail"],
        "flags": a["flags"], "stop": a["stop"], "recency_note": a["recency_note"],
        "enabled": a.get("enabled", {}),
        "verdict": a["verdict"],
    }


def _kline_payload(code, period="day"):
    try:
        rows = data_fetch.fetch_kline(code, limit=260, fast=True, period=period)
    except Exception as e:
        rows = []
    analyses, market = _analyze_all()
    sig = next((a for a in analyses if a["code"] == code), None)
    sig_json = _json_serial(sig) if sig else None
    if not rows:
        return {
            "code": code, "name": (sig_json or {}).get("name", ""),
            "sector": (sig_json or {}).get("sector", ""),
            "kline": [], "ma5": [], "ma10": [], "ma20": [],
            "error": (sig_json or {}).get("error", "数据暂不可用"),
            "signal": sig_json,
        }
    closes = [r["close"] for r in rows]
    def ma_series(n):
        out = []
        for i in range(len(rows)):
            out.append(round(sum(closes[i - n + 1 : i + 1]) / n, 2) if i + 1 >= n else None)
        return out
    kline = [{"date": r["date"], "open": r["open"], "close": r["close"],
              "low": r["low"], "high": r["high"], "vol": r["vol"], "pct": r["pct"]} for r in rows]
    return {
        "code": code, "name": sig_json["name"] if sig_json else code,
        "sector": sig_json["sector"] if sig_json else "",
        "kline": kline, "ma5": ma_series(5), "ma10": ma_series(10), "ma20": ma_series(20),
        "error": "",
        "signal": sig_json,
    }


def analyze_arbitrary(code):
    """对任意股票(无需在自选)实时算主力行为学信号。供"全市场行业浏览"点股票看分析用。
    用当前激活源(新浪)拉K线，跑完整信号引擎。"""
    controls = _control_bits(_enabled())
    try:
        rows = data_fetch.fetch_kline(code, limit=300, fast=True)
    except Exception as e:
        return {"code": code, "name": "", "error": str(e)[:60], "signal": None}
    if not rows:
        name = data_fetch.fetch_stock_basic(code).get("name", "")
        return {"code": code, "name": name, "error": "无数据", "signal": None,
                "stop": {}, "state": "数据待获取"}
    name = data_fetch.fetch_stock_basic(code).get("name", "") or code
    try:
        a = signal_engine.analyze(rows, name=name, code=code, enabled=controls)
        return {"code": code, "name": name, "error": "", "signal": _json_serial(a)}
    except Exception as e:
        return {"code": code, "name": name, "error": str(e)[:60], "signal": None}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, msg, status=400):
        self._send_json({"ok": False, "msg": msg}, status=status)

    def _send_png(self, png_bytes):
        """回传 PNG 图片（用于二维码）。"""
        if not png_bytes:
            self._send_error("no data", 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(png_bytes)

    def _check_origin(self):
        """CSRF 防护：只接受本机来源。POST 必须带合法 Origin/Sec-Fetch-Site，或同源无 Origin。
        返回 (ok, err_msg)。"""
        site = self.headers.get("Sec-Fetch-Site", "")
        origin = self.headers.get("Origin", "")
        if site and site not in ("same-origin", "none"):
            return False, "cross-site origin"
        if origin:
            # Origin 必须指向本机
            try:
                from urllib.parse import urlparse as _up
                o = _up(origin)
                if o.hostname not in (HOST, "localhost"):
                    return False, "bad origin host"
            except Exception:
                return False, "bad origin"
        return True, ""

    def _read_body(self):
        """读取并校验 JSON body。返回 dict；非法时置 self._body_err 并返回 {}/{None}。"""
        ctype = (self.headers.get("Content-Type", "") or "").lower()
        if ctype and "application/json" not in ctype:
            self._body_err = "content-type must be application/json"
            return {}
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._body_err = ""
            return {}
        if length > 2_000_000:  # 2MB 上限，防超大 body
            self._body_err = "body too large"
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._body_err = "invalid json"
            return {}
        self._body_err = ""
        if not isinstance(data, dict):
            self._body_err = "body must be a JSON object"
            return {}
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/stocks":
            analyses, market = _analyze_all()
            self._send_json({
                "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stocks": [_json_serial(a) for a in analyses],
                "market": market,
            })
        elif path.startswith("/api/kline/"):
            code = path.split("/")[-1]
            if not re.fullmatch(r"\d{6}", code):
                self._send_error("invalid code", 400)
                return
            qs = parse_qs(parsed.query)
            period = (qs.get("period", ["day"])[0] or "day")
            if period not in ("day", "week", "month"):
                period = "day"
            payload = _kline_payload(code, period)
            self._send_json(payload if payload else {"error": "no data"})
        elif path == "/api/brain":
            self._send_json({"catalog": brain.skill_catalog(), "enabled": store.get_brain()})
        elif path == "/api/theme":
            self._send_json({"theme": store.get_theme()})
        elif path == "/api/source":
            self._send_json(datasource.list_sources())
        elif path == "/api/schedule":
            self._send_json({"schedule": store.get_schedule()})
        elif path == "/api/refresh_status":
            self._send_json(refresh_mod.status())
        elif path == "/api/search":
            qs = parse_qs(parsed.query)
            q = (qs.get("q", [""])[0] or "").strip()
            # 纯6位数字视为代码直接返回
            if re.fullmatch(r"\d{6}", q):
                self._send_json({"results": [{"code": q, "name": datasource.active_source().fetch_name(q) or q, "market": "auto"}]})
            else:
                self._send_json({"results": datasource.search_stocks(q)})
        elif path == "/api/industry":
            self._send_json({"industries": industry_mod.list_industries()})
        elif path.startswith("/api/industry/"):
            node = path.split("/")[-1]
            # 新浪节点代码以 ~ 开头, 限制字符集防路径/URL注入
            if not re.fullmatch(r"[A-Za-z0-9_~:]+", node) or len(node) > 40:
                self._send_error("invalid node", 400)
                return
            self._send_json({"node": node, "stocks": industry_mod.industry_stocks(node)})
        elif path.startswith("/api/analyze/"):
            code = path.split("/")[-1]
            # 与其他 code 入口一致：仅接受 6 位数字，杜绝路径/文件名注入
            if not re.fullmatch(r"\d{6}", code):
                self._send_error("invalid code", 400)
                return
            self._send_json(analyze_arbitrary(code))
        elif path == "/api/remote":
            self._send_json(remote_mod.status())
        elif path == "/api/remote/qr":
            # 返回公网地址二维码 PNG
            st = remote_mod.status()
            url = st.get("public_url") or ""
            png = remote_mod.make_qr_png(url)
            self._send_png(png)
        elif path == "/api/watchlist":
            self._send_json({"watchlist": store.get_watchlist()})
        elif path in ("/", "/index.html"):
            self._serve_file("index.html")
        elif path == "/echarts.min.js":
            self._serve_file("echarts.min.js")
        elif path.endswith((".js", ".css", ".png", ".svg", ".ico")):
            self._serve_file(path.lstrip("/"))
        else:
            # 未知路径一律 404，绝不交给文件服务（防任意文件读取）
            self._send_error("not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        # CSRF 保护：跨源 POST 直接拒绝
        ok, err = self._check_origin()
        if not ok:
            self._send_error("forbidden: " + err, 403)
            return
        if path not in ("/api/brain", "/api/theme", "/api/watchlist/add", "/api/watchlist/del",
                        "/api/watchlist/order", "/api/refresh", "/api/schedule/set",
                        "/api/remote/tunnel", "/api/remote/password"):
            self._send_error("unknown", 404)
            return
        # 慢速读防护：给底层 socket 设读超时(不是 rfile)，防 Content-Length 虚高挂死
        try:
            self.connection.settimeout(15)
        except Exception:
            pass
        # 无 body 的端点(如 refresh)直接空 body；其余才读 JSON
        if path == "/api/refresh":
            body = {}
        else:
            body = self._read_body()
            if getattr(self, "_body_err", None):
                self._send_error(self._body_err, 400)
                return
        try:
            if path == "/api/brain":
                saved = store.set_brain(body)
                _cache["analyses"] = None  # 使缓存失效
                self._send_json({"ok": True, "enabled": saved})
            elif path == "/api/theme":
                saved = store.set_theme(body.get("theme", "a"))
                self._send_json({"ok": True, "theme": saved})
            elif path == "/api/watchlist/add":
                code = str(body.get("code", "")).strip()
                name = str(body.get("name", "")).strip()
                sector = str(body.get("sector", "")).strip()
                if not code or not re.fullmatch(r"\d{6}", code):
                    self._send_error("代码需为6位数字", 400)
                    return
                wl, msg = store.add_stock(code, name, sector)
                _cache["analyses"] = None
                self._send_json({"ok": True, "msg": msg, "watchlist": wl})
            elif path == "/api/watchlist/del":
                code = str(body.get("code", "")).strip()
                if not re.fullmatch(r"\d{6}", code):
                    self._send_error("invalid code", 400)
                    return
                wl, msg = store.remove_stock(code)
                _cache["analyses"] = None
                self._send_json({"ok": True, "msg": msg, "watchlist": wl})
            elif path == "/api/watchlist/order":
                ordered = body.get("order", [])
                if not isinstance(ordered, list):
                    self._send_error("order must be a list", 400)
                    return
                ordered = [str(c) for c in ordered if re.fullmatch(r"\d{6}", str(c))]
                wl = store.reorder_stock(ordered)
                _cache["analyses"] = None
                self._send_json({"ok": True, "watchlist": wl})
            elif path == "/api/refresh":
                ok, info = refresh_mod.refresh_all()
                analyses, market = _analyze_all()
                self._send_json({"ok": ok, "info": info,
                                 "stocks": [_json_serial(a) for a in analyses], "market": market})
            elif path == "/api/schedule/set":
                sched = store.set_schedule(body)
                self._send_json({"ok": True, "schedule": sched})
            elif path == "/api/remote/tunnel":
                action = str(body.get("action", "")).strip()
                # 隧道指向密码代理端口 8095（代理由 remote_proxy.py 独立进程跑，见 start_remote.cmd）
                if action == "start":
                    r = remote_mod._tunnel.start(8095)
                    self._send_json(r)
                elif action == "stop":
                    self._send_json(remote_mod._tunnel.stop())
                else:
                    self._send_error("action must be start|stop", 400)
            elif path == "/api/remote/password":
                pw = str(body.get("pw", "")).strip()
                if len(pw) < 4:
                    self._send_error("password too short", 400)
                    return
                remote_mod.set_password(pw)
                self._send_json({"ok": True})
            else:
                self._send_error("unknown", 404)
        except Exception as e:
            self._send_error("server error", 500)

    def _serve_file(self, rel):
        """仅服务 WEB_DIR 内的白名单静态文件；任何越界/非白名单一律 404（防任意文件读取）。"""
        if not rel or rel in (".", "..") or os.path.isabs(rel):
            self._send_error("not found", 404)
            return
        # 1) 白名单前缀/后缀
        base = rel.rsplit(".", 1)
        if rel in _SAFE_STATIC:
            pass
        elif not (len(base) == 2 and "." + base[1] in _SAFE_SUFFIX):
            self._send_error("not found", 404)
            return
        # 2) 候选路径
        candidates = [os.path.join(WEB_DIR, rel)]
        if rel == "echarts.min.js":
            candidates.append(os.path.join(WEB_DIR, "node_modules", "echarts", "dist", rel))
        # 3) realpath 必须落在 WEB_DIR 内（防 ../ 逃逸）
        web_real = os.path.realpath(WEB_DIR)
        for fp in candidates:
            fp_real = os.path.realpath(fp)
            try:
                if not fp_real.startswith(web_real + os.sep) and fp_real != web_real:
                    continue
            except Exception:
                continue
            if os.path.isfile(fp_real):
                if fp.endswith(".js") or fp.endswith(".mjs"):
                    ctype = "application/javascript"
                elif fp.endswith(".css"):
                    ctype = "text/css"
                elif fp.endswith(".png"):
                    ctype = "image/png"
                elif fp.endswith(".svg"):
                    ctype = "image/svg+xml"
                elif fp.endswith(".ico"):
                    ctype = "image/x-icon"
                elif fp.endswith(".html"):
                    ctype = "text/html; charset=utf-8"
                else:
                    ctype = "application/octet-stream"
                body = open(fp_real, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                # HTML/JS/CSS 禁用缓存——前端频繁改版, 避免浏览器缓存旧JS导致"点击无反应"
                if fp.endswith((".html", ".js", ".css")):
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(body)
                return
        self._send_error("not found", 404)

    def log_message(self, fmt, *args):
        pass


def main(port=8090):
    # 先做一次启动健康检查(已绑到 _analyze_all, 失败不影响起服务)
    try:
        analyses, market = _analyze_all()
        print(f"✅ 数据就绪：{len(analyses)} 只股票。")
    except Exception as e:
        print(f"⚠️ 启动时数据预取失败(不影响服务): {str(e)[:50]}")
    # 多线程服务器：慢请求(如 /api/refresh)不再阻塞整个站点
    srv = ThreadingHTTPServer((HOST, port), Handler)
    # 启动定时刷新调度线程（读 config.json 的 schedule）
    stop_event = threading.Event()
    sched_thread = threading.Thread(target=refresh_mod.schedule_loop, args=(stop_event, 20), daemon=True)
    sched_thread.start()
    sched = store.get_schedule()
    print(f"🌐 主力行为学平台已启动: http://{HOST}:{port}/")
    print(f"   看板 / 接口: /  /api/stocks  /api/brain  /api/watchlist  /api/schedule")
    print(f"   定时刷新: {'开启, 每天 '+str(sched.get('time','-')) if sched.get('enabled') else '关闭(可在页面开启)'}")
    print("   Ctrl+C 停止。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        stop_event.set()
        srv.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    main(port)
