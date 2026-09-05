# -*- coding: utf-8 -*-
"""
export_data.py — 把自选股的真实行情导出到 data/custom/（供 FileSource 使用）

用法:
  python scripts/export_data.py            # 导出 watchlist.md 里的所有股票
  python scripts/export_data.py 605358 688981   # 只导出指定代码
  python scripts/export_data.py --dry     # 只探测, 不写文件

逻辑:
  1. 用东方财富源(默认) fetch_kline 拉真实复权K线;
  2. 成功 → 写 data/custom/<code>.json(统一rows格式) + data/custom/<code>.name;
  3. 失败(数据源限流) → 不写假数据, 只打日志, 并把失败清单写到 data/custom/_status.json.
  4. 之后把 datasource.json 设为 {"type":"file"} 即可用本地数据, 彻底摆脱限流.
"""
from __future__ import annotations
import os, sys, json, datetime as dt

# Windows 控制台默认 GBK → 强制 UTF-8，否则打印 emoji 崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))
import data_fetch, store

CUSTOM = os.path.join(BASE, "data", "custom")
PERIODS = ["day", "week", "month"]


def _atomic_write(fn, text):
    """写临时文件后原子替换，避免读写方读到半截文件。"""
    import tempfile
    d = os.path.dirname(fn)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, fn)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def export(codes, periods=None, force=False, dry=False):
    os.makedirs(CUSTOM, exist_ok=True)
    periods = periods or PERIODS
    import datasource as _ds
    ok, fail = [], []
    for c in codes:
        name = ""
        # 名称只需取一次
        try:
            name = _ds.TencentSource().fetch_name(c) or ""
        except Exception:
            name = ""
        for period in periods:
            got = None
            src = None
            for s in (_ds.active_source(), _ds.TencentSource()):
                if getattr(s, "name", "") == "file":
                    # 本地源不是实时源，不回退抓取
                    continue
                try:
                    rows = s.fetch_kline(c, limit=320, force=True, use_cache=False, period=period)
                    if rows:
                        got = rows
                        src = s
                        break
                except Exception as e:
                    _ = e
            if got is None:
                fail.append((c, f"{period}: 数据源均失败(限流)"))
                print(f"  ❌ {c} {period}: 导出失败(数据源均不通)")
                continue
            if dry:
                print(f"  {c} {period}: 可抓到 {len(got)} 行, 最新 {got[-1]['date']} close={got[-1]['close']} (源={src.name})")
                ok.append(f"{c}:{period}")
                continue
            stem = f"{c}_{period}" if period != "day" else c
            fn = os.path.join(CUSTOM, f"{stem}.json")
            _atomic_write(fn, json.dumps(got, ensure_ascii=False, indent=1))
            print(f"  ✅ {c} {period}: 导出 {len(got)} 行 ({src.name}源) -> data/custom/{stem}.json")
            ok.append(f"{c}:{period}")
        if name:
            _atomic_write(os.path.join(CUSTOM, f"{c}.name"), name)
    info = {"exported_at": dt.datetime.now().isoformat(), "ok": ok,
            "fail": [{"code": c, "reason": r} for c, r in fail]}
    if not dry:
        json.dump(info, open(os.path.join(CUSTOM, "_status.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    print(f"\n结果: 成功 {len(ok)} / 失败 {len(fail)}  ->  {CUSTOM}/_status.json")
    print("可用命令:")
    print("  python scripts/export_data.py --dry          # 只探测哪些能抓到")
    print("  python scripts/export_data.py 605358 688981   # 只导这几只(日/周/月)")
    print("  python scripts/export_data.py --day-only      # 只导日线")
    print("  导完后把 datasource.json 设为 {\"type\":\"file\"} 并重启 web_server")
    return ok, fail


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv
    if "--day-only" in sys.argv:
        periods = ["day"]
    elif "--week-only" in sys.argv:
        periods = ["week"]
    elif "--month-only" in sys.argv:
        periods = ["month"]
    else:
        periods = PERIODS
    if args:
        codes = [a for a in args if a.isdigit()]
    else:
        codes = [s["code"] for s in store.get_watchlist()]
    if not codes:
        print("无自选股代码(检查 watchlist.md 或传参数)。")
        sys.exit(1)
    export(codes, periods=periods, force=force, dry=dry)
