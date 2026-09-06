# -*- coding: utf-8 -*-
"""
run.py — 主力行为学平台 主入口
用法:
  python run.py              # 读取 watchlist.md, 全量分析并输出到 output/
  python run.py 605358       # 只分析指定代码(可多个)
  python run.py --refresh    # 强制刷新数据缓存
"""
from __future__ import annotations
import os, sys, re, datetime as dt

# Windows 控制台默认 GBK，无法打印 emoji/中文符号 → 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 让 src 可 import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import data_fetch
import signal_engine
import md_render
import context_engine
import store  # 自选股真相源：config.json(运行时) 优先，config 空时回退 watchlist.md 示例清单


def _strip_dir(path):
    return path.replace("\\", "/").split("/")[-1]


def _watch_tasks():
    """自选任务 [(code,name,sector),...]：读 store（config.json），与网页共用同一真相源。
    config 无自选时 store 自动回退 watchlist.md（仓库示例初始清单）。"""
    return [(s["code"], s.get("name", ""), s.get("sector", ""))
            for s in store.get_watchlist()]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--refresh" in sys.argv

    # 收集要分析的代码
    if args:
        codes = [a for a in args if re.fullmatch(r"\d{6}", a)]
        watch = {c: (n, s) for c, n, s in _watch_tasks()}
        tasks = []
        for c in codes:
            n, s = watch.get(c, ("", ""))
            tasks.append((c, n, s))
    else:
        tasks = []
        seen = set()
        for c, n, s in _watch_tasks():
            if c in seen:
                continue
            seen.add(c)
            tasks.append((c, n, s))
        if not tasks:
            print("自选为空（config.json 与 watchlist.md 示例均为空），无法分析。")
            return

    print(f"今日 {dt.date.today().isoformat()}，待分析 {len(tasks)} 只。")
    sector_map = {code: sector for code, name, sector in tasks}
    results = []
    for code, name, sector in tasks:
        try:
            rows = data_fetch.fetch_kline(code, limit=300, force=force)
            if not rows:
                print(f"  ⚠️ {code} 无数据，跳过")
                continue
            if not name:
                info = data_fetch.fetch_stock_basic(code)
                name = info.get("name", "") or code
            a = signal_engine.analyze(rows, name=name or code, code=code)
            a["sector"] = sector
            a["_rows"] = rows  # 供 context_engine 复用，避免重复抓取
            results.append(a)
        except Exception as e:
            print(f"  ❌ {code} 分析失败: {e}")

    # ---- B层 市场上下文（龙头定高度/板块轮动/鲸落/后四分之一）----
    index_rows = None
    try:
        index_rows = data_fetch.fetch_kline("399905", limit=120)  # 中证1000 作为真实参照系
    except Exception as e:
        index_rows = None
    market = context_engine.analyze_context(results, sector_map, index_rows)

    # ---- 渲染 ----
    if results:
        for a in results:
            md_render.write_output(a, a.get("sector", ""), market)
        board = md_render.render_watchlist_board(results, market)
        board_path = os.path.join(md_render.OUTPUT_DIR, "自选股看板.md")
        with open(board_path, "w", encoding="utf-8") as f:
            f.write(board)
        # 打印摘要
        for a in results:
            v = a["verdict"]
            print(f"  {a['code']} {a['name']}: [{a['state']}] → {v['label']}")
        print("\n✅ 完成。个股详情页 + 看板(含市场环境)已写入 " + md_render.OUTPUT_DIR)


if __name__ == "__main__":
    main()
