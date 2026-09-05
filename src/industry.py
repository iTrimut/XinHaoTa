# -*- coding: utf-8 -*-
"""
industry.py — 全市场行业板块(新浪财经, 稳定源)

提供:
  - list_industries()     行业板块列表 [{code,name,count}, ...]
  - industry_stocks(node) 某行业成分股 [{code,name,price,pct}, ...] (按涨跌幅排序)

数据源: vip.stock.finance.sina.com.cn (新狼行业分类, 稳定, 不易限流)
本地缓存 data/industry/ 以兜底。仅用于"全市场行业浏览", 不参与自选股信号。
"""
from __future__ import annotations
import os, json, re, datetime as dt
import urllib.request, urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IND_DIR = os.path.join(BASE, "data", "industry")

LIST_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
NODE_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page={page}&num={num}&sort=changepercent&asc=0&node={node}")


def _get(url, enc="utf-8", timeout=15):
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout).read()
    return raw.decode(enc, errors="ignore")


def _cache_file(name):
    os.makedirs(IND_DIR, exist_ok=True)
    return os.path.join(IND_DIR, name)


def list_industries():
    """行业板块列表 [{code,name,count},...]。优先缓存(当天), 失败回退缓存。"""
    cache = _cache_file("industries.json")
    if os.path.exists(cache):
        c = json.load(open(cache, encoding="utf-8"))
        if c.get("fetch_date") == dt.date.today().isoformat() and c.get("data"):
            return c["data"]
    try:
        txt = _get(LIST_URL, enc="gbk")
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0))
        out = []
        for k, v in data.items():
            parts = v.split(",")
            name = parts[1] if len(parts) > 1 else k
            count = parts[2] if len(parts) > 2 else ""
            if count.isdigit():
                out.append({"code": k, "name": name, "count": int(count)})
        out.sort(key=lambda x: -x["count"])
        json.dump({"fetch_date": dt.date.today().isoformat(), "data": out},
                  open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out
    except Exception:
        if os.path.exists(cache):
            return json.load(open(cache, encoding="utf-8")).get("data", [])
        return []


def industry_stocks(node, limit=40):
    """行业成分股 [{code,name,price,pct},...] 按涨跌幅降序。失败回退缓存。
    price/pct 一律转 float(新浪 trade/changepercent 可能是字符串)，坏缓存(价格全0)丢弃重抓。"""
    cache = _cache_file(f"node_{node}.json")
    if os.path.exists(cache):
        c = json.load(open(cache, encoding="utf-8"))
        data = c.get("data") or []
        nonempty = [s for s in data if s.get("price")]
        is_today = c.get("fetch_date") == dt.date.today().isoformat()
        # 坏缓存判定: ①非当天 ②当天但价格全0(接口异常抓的) ③当天但条数过少(<12, 网络抖动抓的半截) → 都不信任, 重抓
        good = is_today and data and nonempty and len(data) >= 12
        if good:
            return data[:limit]
    # 网络抓取(带重试, 新浪偶发超时)
    import time as _t
    url = NODE_URL.format(page=1, num=limit, node=node)
    last_err = None
    for _attempt in range(3):
        try:
            txt = _get(url, enc="utf-8")
            arr = json.loads(txt)
            out = []
            for x in arr:
                if not x.get("code"):
                    continue
                try:
                    price = float(x.get("trade") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                try:
                    pct = float(x.get("changepercent") or 0)
                except (TypeError, ValueError):
                    pct = 0.0
                out.append({"code": x["code"], "name": x.get("name", ""),
                            "price": price, "pct": pct, "symbol": x.get("symbol", "")})
            if out:
                json.dump({"fetch_date": dt.date.today().isoformat(), "data": out},
                          open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                return out[:limit]
            last_err = "empty"
        except Exception as e:
            last_err = str(e)[:60]
            _t.sleep(1.5)
    # 全部失败: 回退到已有缓存(不论是否今天), 尽量有数据而不是空白
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8")).get("data", [])[:limit]
    return []


def stats():
    """前端展示用: 行业总数 + 缓存情况。"""
    inds = list_industries()
    cached = 0
    if os.path.isdir(IND_DIR):
        cached = len([f for f in os.listdir(IND_DIR) if f.startswith("node_")])
    return {"industries": len(inds), "industry_cached": cached}
