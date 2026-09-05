# -*- coding: utf-8 -*-
"""
datasource.py — 数据源接口（可插拔）

把"行情从哪里来"抽象成一个接口，让平台不再被单一数据源(东方财富反爬/限流)绑架——
以后用户有了自己的数据，**不用改任何代码**，只需切一个配置即可接入。

接口约定（所有 source 都必须实现 fetch_kline 并返回**统一格式**的 rows）:
  rows = [ {date:'YYYY-MM-DD', open:float, close:float, high:float, low:float, vol:float, amt:float, pct:float, to:float}, ... ]
  升序。date 为字符串，其余 float。

内置数据源:
  - EastmoneySource   东方财富免费复权K线(默认)。含 fast 快速失败模式 + 本地缓存。
  - FileSource        读本地 JSON/CSV 文件，无需网络。方便用户直接丢自己的数据。
  - HttpSource        用户指定的 HTTP API(返回统一格式 JSON)。方便自定义服务。

切换方式: 编辑项目根的 datasource.json，如 {"type":"file","data_dir":"data/custom"}
然后重启 web_server / 重新跑 run.py。
"""
from __future__ import annotations
import os, json, glob, csv, datetime as dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
CACHE_DIR = os.path.join(DATA_DIR, "kline")
CONFIG = os.path.join(BASE, "datasource.json")

# 统一 rows 的必需字段
REQ_FIELDS = ("date", "open", "close", "high", "low")


# ---------- 基类 ----------
class DataSource:
    name = "base"

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        """返回统一格式 rows（升序）。period 支持 day/week/month。实现方决定是否缓存。"""
        raise NotImplementedError

    def fetch_name(self, code):
        return ""


# ---------- 东方财富（默认） ----------
class EastmoneySource(DataSource):
    name = "eastmoney"
    BASE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def _http_get(self, url, timeout=25, retries=4, wait=1.5):
        import time, urllib.request, json as _j
        last = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return _j.loads(resp.read().decode("utf-8"))
            except Exception as e:
                last = e
                time.sleep(wait + attempt * 0.5)
        raise RuntimeError(f"HTTP 失败(重试{retries}): {last}")

    @staticmethod
    def secid_for(code):
        code = code.strip()
        if code.startswith(("60", "68", "90", "51", "58", "11")):
            return f"1.{code}"
        if code.startswith(("00", "30", "20", "15", "12")):
            return f"0.{code}"
        return f"1.{code}" if code[0] in "69" else f"0.{code}"

    _KLT = {"day": "101", "week": "102", "month": "103"}

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        os.makedirs(CACHE_DIR, exist_ok=True)
        klt = self._KLT.get(period, "101")
        cache_file = os.path.join(CACHE_DIR, f"{code}_{period}.json" if period != "day" else f"{code}.json")
        today = dt.date.today().isoformat()
        if use_cache and not force and os.path.exists(cache_file):
            cached = json.load(open(cache_file, encoding="utf-8"))
            if cached.get("fetch_date") == today and len(cached.get("rows", [])) >= 30:
                return cached["rows"]
        import urllib.parse
        params = {"secid": self.secid_for(code), "fields1": "f1,f2,f3,f4,f5,f6",
                  "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                  "klt": klt, "fqt": "1", "beg": "0", "end": "20500101", "lmt": str(limit)}
        url = self.BASE_URL + "?" + urllib.parse.urlencode(params)
        payload = self._http_get(url, retries=1 if fast else 4, timeout=5 if fast else 25)
        if not payload.get("data") or not payload["data"].get("klines"):
            if os.path.exists(cache_file):
                return json.load(open(cache_file, encoding="utf-8")).get("rows", [])
            return []
        rows = []
        for line in payload["data"]["klines"]:
            p = line.split(",")
            rows.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                         "high": float(p[3]), "low": float(p[4]), "vol": float(p[5]),
                         "amt": float(p[6]), "pct": float(p[8]), "to": float(p[10])})
        rows.sort(key=lambda r: r["date"])
        json.dump({"code": code, "fetch_date": today, "rows": rows},
                  open(cache_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return rows

    def fetch_name(self, code):
        try:
            import urllib.request, json as _j
            url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={self.secid_for(code)}"
                   f"&fields=f58&invt=2")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            d = _j.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8")).get("data") or {}
            return d.get("f58", "")
        except Exception:
            return ""


# ---------- 新浪（首选稳定源） ----------
class SinaSource(DataSource):
    name = "sina"
    """新浪前复权K线，quotes.sina.cn。稳定、不受东方财富/腾讯限流影响。三个周期均有。
     日 scale=240, 周 scale=1200, 月 scale=7200。返回前复权(与腾讯一致)。"""

    _KLT = {"day": "240", "week": "1200", "month": "7200"}

    @staticmethod
    def secid(code):
        code = code.strip()
        prefix = "sh" if code.startswith(("60", "68", "90", "51", "58", "11")) else "sz"
        return f"{prefix}{code}"

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        os.makedirs(CACHE_DIR, exist_ok=True)
        scale = self._KLT.get(period, "240")
        cache_file = os.path.join(CACHE_DIR, f"{code}_sina_{period}.json")
        today = dt.date.today().isoformat()
        if use_cache and not force and os.path.exists(cache_file):
            cached = json.load(open(cache_file, encoding="utf-8"))
            if cached.get("fetch_date") == today and len(cached.get("rows", [])) >= 20:
                return cached["rows"]
        import urllib.request, json as _j, re, time
        sec = self.secid(code)
        datalen = min(max(limit, 100), 320)  # 新浪 datalen 上限约 320
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_/CN_MarketDataService.getKLineData"
               f"?symbol={sec}&scale={scale}&ma=no&datalen={datalen}")
        last = None
        for attempt in range(2):
            try:
                raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read()
                txt = raw.decode("utf-8", errors="ignore")
                m = re.search(r"\[.*\]", txt, re.S)
                if not m:
                    raise ValueError("no kline array")
                arr = _j.loads(m.group(0))
                rows = []
                prev = None
                for it in arr:
                    c = float(it["close"])
                    pct = round((c - prev) / prev * 100, 2) if prev else 0.0
                    rows.append({"date": str(it["day"]), "open": float(it["open"]), "close": c,
                                 "high": float(it["high"]), "low": float(it["low"]),
                                 "vol": float(it.get("volume", 0) or 0), "amt": 0.0, "pct": pct, "to": 0.0})
                    prev = c
                rows.sort(key=lambda r: r["date"])
                json.dump({"code": code, "fetch_date": today, "period": period, "rows": rows},
                          open(cache_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                return rows
            except Exception as e:
                last = e
                time.sleep(1.5)
        raise RuntimeError(f"新浪K线失败: {last}")

    def fetch_name(self, code):
        try:
            import urllib.request, json as _j
            sec = self.secid(code)
            # 新浪实时行情 qt.gtimg 也稳定, 用腾讯取名称作为兜底
            u = f"https://qt.gtimg.cn/q={sec}"
            raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=10).read()
            txt = raw.decode("gbk", errors="ignore")
            if "~" in txt:
                seg = txt.split("~", 2)
                return seg[1] if len(seg) > 1 else ""
            return ""
        except Exception:
            return ""


# ---------- 腾讯（备用，东方财富限流时用） ----------
class TencentSource(DataSource):
    name = "tencent"
    """腾讯前复权日K，web.ifzq.gtimg.cn。字段: [date, open, close, high, low, vol]。
    通常不受东方财富限流影响，作为可靠性兜底。"""

    @staticmethod
    def secid(code):
        code = code.strip()
        prefix = "sh" if code.startswith(("60", "68", "90", "51", "58", "11")) else "sz"
        return f"{prefix}{code}"

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CACHE_DIR, f"{code}_tx_{period}.json")
        today = dt.date.today().isoformat()
        if use_cache and not force and os.path.exists(cache_file):
            cached = json.load(open(cache_file, encoding="utf-8"))
            if cached.get("fetch_date") == today and len(cached.get("rows", [])) >= 20:
                return cached["rows"]
        sec = self.secid(code)
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},{period},,,{limit},qfq")
        import urllib.request, json as _j, time
        last = None
        for attempt in range(2):  # 腾讯较稳, 2次足够
            try:
                d = _j.loads(urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12).read())
                data = d.get("data", {}).get(sec) or {}
                kl = data.get("qfq" + period) or data.get(period) or data.get("qfqday") or data.get("day") or []
                rows = []
                prev_close = None
                for item in kl:
                    date, o, c, h, l, v = str(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5])
                    pct = round((c - prev_close) / prev_close * 100, 2) if prev_close else 0.0
                    rows.append({"date": date, "open": o, "close": c, "high": h, "low": l,
                                 "vol": v, "amt": 0.0, "pct": pct, "to": 0.0})
                    prev_close = c
                rows.sort(key=lambda r: r["date"])
                json.dump({"code": code, "fetch_date": today, "period": period, "rows": rows},
                          open(cache_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                return rows
            except Exception as e:
                last = e
                time.sleep(1.5)
        raise RuntimeError(f"腾讯K线失败: {last}")

    def fetch_name(self, code):
        try:
            import urllib.request, json as _j
            sec = self.secid(code)
            # 用腾讯实时摘要接口拿名称
            u = f"https://qt.gtimg.cn/q={sec}"
            raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=10).read()
            txt = raw.decode("gbk", errors="ignore")
            # 格式: v_sh605358="1~贵州茅台~600519~..."; 名称在~分隔第2位
            if "~" in txt:
                seg = txt.split("~", 2)
                return seg[1] if len(seg) > 1 else ""
            return ""
        except Exception:
            return ""


# ---------- 本地文件源（用户挂自己的数据 / 断网可用） ----------
class FileSource(DataSource):
    name = "file"
    """读 data/custom/<code>.json 或 <code>.csv。
      JSON 格式: [ {"date","open","close","high","low","vol"(可选),...} ] 或 {"code":"..","rows":[...]}
      CSV 格式: 需有表头 date,open,close,high,low[,vol,...]
    """

    def __init__(self, data_dir=None):
        self.data_dir = data_dir or os.path.join(DATA_DIR, "custom")

    def _load_file(self, code, period="day"):
        # 只认 period 专属文件；非 day 周期缺失时返回 []（绝不拿 day 冒充周/月）
        stems = [f"{code}_{period}"] if period != "day" else [code]
        for stem in stems:
            for ext in ("json", "csv"):
                fp = os.path.join(self.data_dir, f"{stem}.{ext}")
                if os.path.exists(fp):
                    return self._parse(fp, ext)
        return []

    def _parse(self, fp, ext):
        if ext == "json":
            data = json.load(open(fp, encoding="utf-8"))
            rows = data.get("rows", data) if isinstance(data, dict) else data
        else:  # csv
            rows = []
            with open(fp, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    rows.append({k: v for k, v in row.items()})
        out = []
        for r in rows:
            try:
                out.append({
                    "date": str(r["date"]), "open": float(r["open"]), "close": float(r["close"]),
                    "high": float(r["high"]), "low": float(r["low"]),
                    "vol": float(r.get("vol", 0) or 0), "amt": float(r.get("amt", 0) or 0),
                    "pct": float(r.get("pct", 0) or 0), "to": float(r.get("to", 0) or 0),
                })
            except Exception:
                continue
        out.sort(key=lambda r: r["date"])
        return out

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        rows = self._load_file(code, period)
        return rows[-limit:] if rows else []

    def fetch_name(self, code):
        # 可选: data/custom/<code>.name.json 或 .name.txt
        for fp in (os.path.join(self.data_dir, f"{code}.name"),
                   os.path.join(self.data_dir, f"{code}.name.json")):
            if os.path.exists(fp):
                return open(fp, encoding="utf-8").read().strip()
        return ""


# ---------- HTTP JSON 源（用户自定服务） ----------
class HttpSource(DataSource):
    name = "http"
    """配置 {"type":"http","url_template":"http://myapi/data/{code}?limit={limit}"}
      url_template 中 {code} / {limit} 会被替换。返回统一格式 rows JSON。"""

    def __init__(self, url_template=""):
        self.url_template = url_template

    def fetch_kline(self, code, limit=260, use_cache=True, force=False, fast=False, period="day"):
        if not self.url_template:
            return []
        import urllib.request, json as _j
        url = self.url_template.format(code=code, limit=limit)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            d = _j.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8"))
            rows = d.get("rows", d) if isinstance(d, dict) else d
            out = []
            for r in rows:
                out.append({"date": str(r["date"]), "open": float(r["open"]), "close": float(r["close"]),
                            "high": float(r["high"]), "low": float(r["low"]),
                            "vol": float(r.get("vol", 0) or 0), "amt": float(r.get("amt", 0) or 0),
                            "pct": float(r.get("pct", 0) or 0), "to": float(r.get("to", 0) or 0)})
            out.sort(key=lambda r: r["date"])
            return out
        except Exception:
            return []


# ---------- 注册表 / 工厂 ----------
_REGISTRY = {EastmoneySource.name: EastmoneySource, TencentSource.name: TencentSource,
             SinaSource.name: SinaSource, FileSource.name: FileSource, HttpSource.name: HttpSource}

_ACTIVE = None


def _read_config():
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG, encoding="utf-8"))
        except Exception:
            return {}
    return {"type": "sina"}


def active_source():
    """返回当前启用的数据源实例（带进程级缓存）。配置在 datasource.json。
    默认 sina(新浪前复权K线, 稳定)；东财/腾讯/文件/HTTP 可切换。"""
    global _ACTIVE
    if _ACTIVE is None:
        cfg = _read_config()
        typ = cfg.get("type", "sina")
        if typ == "file":
            _ACTIVE = FileSource(cfg.get("data_dir"))
        elif typ == "http":
            _ACTIVE = HttpSource(cfg.get("url_template", ""))
        elif typ == "tencent":
            _ACTIVE = TencentSource()
        elif typ == "eastmoney":
            _ACTIVE = EastmoneySource()
        else:
            _ACTIVE = SinaSource()
    return _ACTIVE


# A股个股代码前缀(排除 ETF/基金/指数/债券)
_SH_STOCK = ("600", "601", "603", "605", "688", "689")
_SZ_STOCK = ("000", "001", "002", "003", "300", "301")


def _is_stock(market, code):
    """是否 A 股个股(非 ETF/指数/基金/债券)。"""
    if market == "sh":
        return code.startswith(_SH_STOCK)
    if market == "sz":
        return code.startswith(_SZ_STOCK)
    return False


def search_stocks(keyword, limit=8):
    """按名称/代码/拼音全拼/拼音首字母 模糊搜索 A股个股（腾讯 smartbox 接口）。
    返回 [{code,name,market}, ...]，失败返回 []。会过滤掉 ETF/指数/基金/债券。"""
    import urllib.request, urllib.parse
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    try:
        u = "https://smartbox.gtimg.cn/s3/?v=2&q=" + urllib.parse.quote(keyword) + "&t=all"
        raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12).read()
        txt = raw.decode("gbk", errors="ignore")
        out = []
        if 'v_hint="' in txt:
            body = txt.split('v_hint="')[1].split('"')[0]
            for it in body.split("^"):
                seg = it.split("~")
                if len(seg) < 3:
                    continue
                market, code, name = seg[0], seg[1], _unescape(seg[2])
                if _is_stock(market, code):
                    out.append({"code": code, "name": name, "market": market})
                if len(out) >= limit:
                    break
        # 若是6位纯代码且能识别为股票, 也加入
        if not out and keyword.isdigit() and len(keyword) == 6:
            market = "sh" if keyword.startswith(_SH_STOCK) else ("sz" if keyword.startswith(_SZ_STOCK) else "")
            if market:
                out.append({"code": keyword, "name": "", "market": market})
        return out
    except Exception:
        return []


def _unescape(s):
    """解码转义的 unicode 序列。"""
    import re
    try:
        return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    except Exception:
        return s


def list_sources():
    """给前端/文档用的数据源清单。"""
    cfg = _read_config()
    return {
        "current": {"type": cfg.get("type", "sina"), "config": cfg},
        "available": [
            {"type": "sina", "desc": "新浪前复权K线(默认, 稳定不受限流, 含日/周/月)", "needs_data": False},
            {"type": "tencent", "desc": "腾讯前复权日K(备用)", "needs_data": False},
            {"type": "eastmoney", "desc": "东方财富免费复权K线(易被反爬限流)", "needs_data": False},
            {"type": "file", "desc": "本地文件 data/custom/<code>.json|csv (无网络, 断网可用)", "needs_data": True},
            {"type": "http", "desc": "自定义HTTP API, 返回统一格式rows JSON", "needs_data": True},
        ],
        "rows_format": "date,open,close,high,low,vol,amt,pct,to",
    }
