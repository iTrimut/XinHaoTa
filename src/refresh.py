# -*- coding: utf-8 -*-
"""
refresh.py — 刷新调度核心

提供一个线程安全的"重新导出真实行情 + 重算分析"操作，供：
  1. 网页"🔄 刷新数据"按钮（立即更新）
  2. 定时调度器（到点自动触发）
共用同一套逻辑，避免重复实现。

refresh_all():
  - 从实时源(腾讯, 东方财富限流兜底)重新拉 日/周/月 数据覆盖 data/custom/
  - 清空分析缓存，重算所有自选股信号
  - 用 lock 防止按钮点击与定时任务互相覆盖
"""
from __future__ import annotations
import os, sys, json, threading, datetime as dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "src"))
import store

_lock = threading.Lock()
_last = {"at": None, "ok": None, "message": None, "running": False}


def refresh_all(codes=None):
    """重新导出实时行情到 data/custom/ 并重算。返回 (ok, summary_dict)。
    线程安全：同时只有一个 refresh 在跑。"""
    if not _lock.acquire(blocking=False):
        return False, {"message": "已有刷新在跑, 请稍候", "running": True}
    try:
        _last["running"] = True
        import export_data  # scripts/export_data.py
        if codes is None:
            codes = [s["code"] for s in store.get_watchlist()]
        ok, fail = export_data.export(codes, force=True)
        _cache_clear()
        at = dt.datetime.now().isoformat()
        _last.update({"at": at, "ok": ok, "message": f"成功 {len(ok)} / 失败 {len(fail)}", "running": False})
        return True, _last
    finally:
        _lock.release()


def _cache_clear():
    """清空 web_server 的分析进程缓存(如有)。用属性缓存避免循环 import。"""
    import web_server as _ws
    # 直接清 _cache 的 analyses/market，让下次 /api/stocks 重新算
    if hasattr(_ws, "_cache"):
        _ws._cache["analyses"] = None
        _ws._cache["market"] = None


def status():
    return dict(_last)


# ---------- 定时调度 ----------
def schedule_loop(stop_event, interval=20):
    """后台线程：每 interval 秒检查是否到"定时刷新"时间，到点触发 refresh_all()。
    读取 store.get_schedule()：{"enabled":bool, "time":"HH:MM"}，每天到点刷新一次。"""
    import time
    last_fired_date = None
    while not stop_event.is_set():
        try:
            sched = store.get_schedule()
            if sched.get("enabled"):
                now = dt.datetime.now()
                target = sched.get("time", "00:00")
                if now.strftime("%H:%M") == target and last_fired_date != now.date():
                    print(f"  ⏰ 定时刷新触发 {target} ...")
                    refresh_all()
                    last_fired_date = now.date()
        except Exception as e:
            print(f"  ⏰ 定时刷新出错: {e}")
        stop_event.wait(interval)
