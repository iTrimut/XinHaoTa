# -*- coding: utf-8 -*-
"""
data_fetch.py — 数据层
拉取 A 股(个股/指数)的复权日线 K 线(东方财富免费接口)，缓存到本地，供信号引擎使用。

数据源: push2his.eastmoney.com (免费、无需 token)
  - 个股: secid = 1.600000(沪) / 0.000001(深)
  - 指数: secid = 1.000001(沪指) / 0.399106(深证成指) / 0.399905(中证1000)
  - 前复权 fqt=1
"""
from __future__ import annotations
import json, os, time, datetime as dt
import urllib.request, urllib.parse

import datasource  # 可插拔数据源注册表

DATA_DIR = os.path.dirname(datasource.DATA_DIR) if hasattr(datasource, "DATA_DIR") else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_DIR = datasource.CACHE_DIR
BASE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _http_get(url: str, timeout: int = 25, retries: int = 4, wait: float = 1.5):
    """带重试/退避的 GET，返回反序列化 dict。对抗 anti-scrape 的瞬断/限流。
    fast 调用方传 timeout=5, retries=1 以在数据源被限流时快速失败返回，避免阻塞主流程。"""
    import time
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(wait + attempt * 0.5)
    raise RuntimeError(f"HTTP 请求失败(重试{retries}次): {last_err}")


def _write_cache(code: str, rows: list):
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    json.dump({"code": code, "fetch_date": today, "rows": rows},
              open(os.path.join(CACHE_DIR, f"{code}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def secid_for(code: str) -> str:
    """把6位代码转成东方财富 secid。规则: 6开头/9开头→沪(1)，0/3开头→深(0)。"""
    code = code.strip()
    if code.startswith(("60", "68", "90", "51", "58", "11")):
        return f"1.{code}"
    if code.startswith(("00", "30", "20", "15", "12")):
        return f"0.{code}"
    if code.startswith(("88", "83", "87", "43")):
        return f"0.{code}"  # 北交所近似,优先按深
    # 兜底: 按首位数字猜
    return f"1.{code}" if code[0] in "69" else f"0.{code}"


def fetch_kline(code: str, limit: int = 260, use_cache: bool = True, force: bool = False, fast: bool = False, period: str = "day"):
    """拉取单只代码的复权日线，返回 [{date,open,close,high,low,vol,amt,pct,to}, ...] 升序。
    委托给 datasource.active_source()（默认为东方财富；可切 file/http 等，见 datasource.json）。
    period 支持 day/week/month。"""
    return datasource.active_source().fetch_kline(code, limit=limit, use_cache=use_cache, force=force, fast=fast, period=period)


def clear_cache():
    """清空 K 线缓存目录（手动刷新时用，强制重新网络抓取）。"""
    import shutil
    if os.path.isdir(CACHE_DIR):
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_stock_basic(code: str):
    """拉取个股名称/板块(简版)，委托给当前数据源。失败返回空 dict，不阻塞主流程。"""
    try:
        name = datasource.active_source().fetch_name(code)
        return {"name": name, "price": 0, "pct": 0}
    except Exception:
        return {}


def fetch_many(codes, limit: int = 260, force: bool = False, pause: float = 0.8, fast: bool = False):
    """批量抓取，单只失败不中断整体；每次请求间加 pause 秒，规避 anti-scrape。
    返回 dict {code: rows}，无数据的 code 不出现。"""
    import time
    out = {}
    for c in codes:
        try:
            rows = fetch_kline(c, limit=limit, force=force, fast=fast)
            if rows:
                out[c] = rows
        except Exception as e:
            print(f"    ⚠️ {c} 抓取失败: {str(e)[:60]}")
        time.sleep(pause)
    return out


if __name__ == "__main__":
    for c in ["605358", "000001", "399106"]:
        rows = fetch_kline(c, limit=30, force=True)
        print(f"{c}: {len(rows)} 条, 最新 {rows[-1]['date']} close={rows[-1]['close']}")
