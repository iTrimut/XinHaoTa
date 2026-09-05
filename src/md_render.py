# -*- coding: utf-8 -*-
"""
md_render.py — 输出层
把信号引擎结果渲染成 Obsidian 友好的 Markdown 个股详情页 + 自选股看板。

亮点: 面向普通投资者——结论卡在前(买/卖/观望 三态+理由)，数值依据在后，风险提示单独成块。
"""
from __future__ import annotations
import os, datetime as dt

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

ACTION_ICON = {"buy": "🟢", "watch": "🟡", "sell": "🔴", "hold": "🟦"}


def _badge(state: str) -> str:
    s = state.split("(")[0]
    icon = {"④线上持股": "🟢", "①倒车接人": "🟢", "②至暗时刻": "🔴", "③线下持币": "⚪"}.get(s, "🟡")
    return f"{icon} **{state}**"


def render_stock_page(a: dict, sector: str = "", market: dict = None) -> str:
    """生成单只股票详情页 markdown。market 为可选的市场上下文引擎输出。"""
    v = a["verdict"]
    icon = ACTION_ICON.get(v["action"], "🟡")
    lines = []
    lines.append("---")
    lines.append("tags: [主力行为学, 个股分析, 信号]")
    lines.append("类型: 个股详情")
    lines.append("状态: 待复核")
    lines.append(f"created: {a['last_date']}")
    lines.append(f"updated: {dt.date.today().isoformat()}")
    lines.append(f"source: 东方财富复权K线 (secid 自动, 截至 {a['last_date']} 收盘)")
    lines.append("---")
    lines.append("")
    lines.append(f"# {a['name']}（{a['code']}）主力行为学分析")
    lines.append("")
    lines.append(f"> 📅 数据截至 **{a['last_date']}**  ·  收盘 **{a['last_close']:.2f}**  ·  涨跌 **{a['last_pct']:+.2f}%**  ·  换手 **{a['last_turnover']:.2f}%**")
    if sector:
        lines.append(f"> 🏷️ 板块：{sector}")
    lines.append("")
    lines.append("## 🎯 核心结论")
    lines.append("")
    lines.append(f">{icon} **{v['label']}**")
    lines.append(">")
    lines.append(f"> {v['reason']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 🧭 当前状态")
    lines.append("")
    lines.append(f"- **主力行为学状态**：{_badge(a['state'])}")
    lines.append(f"  - {a['state_reason']}")
    lines.append(f"- **买卖模式**：**{a['mode']}**")
    lines.append(f"  - {a['mode_reason']}")
    lines.append("")
    lines.append(f"- **时空共振**：{a['space_detail']}")
    lines.append("")
    lines.append("## 📐 均线一览")
    lines.append("")
    lines.append("| 均线 | 数值 | 位置 |")
    lines.append("|------|------|------|")
    for key, label in [("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20")]:
        val = a.get(key)
        if val is None:
            lines.append(f"| {label} | 数据不足 | - |")
            continue
        pos = "上方" if a["last_close"] >= val else "下方"
        lines.append(f"| {label} | {val:.2f} | 价格在{pos} |")
    lines.append("")
    lines.append("## ✅ 排除法检查（必跌特征）")
    lines.append("")
    for flag, reason in a["flags"]:
        mark = "❌" if flag != "无排除信号" else "✅"
        lines.append(f"- {mark} {flag}：{reason}")
    lines.append("")
    lines.append("## 🎯 买卖点（条件化）")
    lines.append("")
    s = a["stop"]
    lines.append("| 项目 | 价位 |")
    lines.append("|------|------|")
    lines.append(f"| 现价 | {s['现价']:.2f} |")
    lines.append(f"| 参考入场(5日线) | {s['参考入场(5日线)']:.2f} |")
    lines.append(f"| **止损价(-3%)** | **{s['止损价(-3%)']:.2f}** |")
    lines.append(f"| 近20日支撑 | {s['近20日支撑']:.2f} |")
    lines.append(f"| 近20日压力 | {s['近20日压力']:.2f} |")
    lines.append("")
    lines.append("## 🧠 行为学提示")
    lines.append("")
    lines.append(f"- {a['recency_note']}")
    lines.append(f"- **盈亏同源**：接受一个缺陷——买(等回踩)=可能错过反弹的踏空；卖(跌破就走)=可能卖飞。两者择一，两头都想占=做不到。")
    lines.append("- **叶公好龙**：看好它，等真回踩给机会时别又不敢进——按上面条件化买卖点机械执行。")
    lines.append("")
    if market:
        lines += market_section(market)
    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ **数据与免责**：本文由规则引擎按主力行为学自动生成，非荐股。请以实时盘面为准；买卖决策与结果自负。")
    return "\n".join(lines) + "\n"


def market_section(market: dict) -> list:
    """市场环境（B层 4技能）渲染成 markdown 行片段。"""
    out = []
    out.append("## 🌍 市场环境（主力行为学 B 层）")
    out.append("")
    h = market.get("height", {})
    if h.get("available"):
        if h.get("leader_found"):
            flag = "🟢"
            head = f"龙头 **{h['leader']}**（相对强度 {h['leader_rs']:.2f}，趋势未破）"
        else:
            flag = "🔴"
            head = f"⚠️ 无真龙头（样本均破位/在均线下），参考 **{h['leader']}**（相对强度 {h['leader_rs']:.2f}）"
        out.append(f"- **龙头定高度**：{flag} {head}。{h['note']}")
    else:
        out.append(f"- **龙头定高度**：数据不足（{h.get('note','')}）")
    r = market.get("rotation", {})
    if r.get("available"):
        out.append(f"- **板块轮动**：{r['wearing_pants']}（刚涨完≈休息）⇄ {r['waiting_pants']}（刚跌完≈轮到）。{r['note']}")
    else:
        out.append(f"- **板块轮动**：{r.get('note','数据不足，请在 watchlist 标注板块')}")
    w = market.get("whale", {})
    if w.get("available"):
        flag = "🐋" if w.get("whale_falling") else "⏳"
        out.append(f"- **鲸落万物弹**：{flag} {w['note']}")
    q = market.get("quarter", {})
    if q.get("available"):
        out.append(f"- **后四分之一**：{q['note']}")
    out.append("")
    return out


def render_watchlist_board(stocks: list, market: dict = None, date_str: str = "") -> str:
    """生成自选股看板 markdown（一屏看全部票的三态结论 + 市场环境）"""
    date_str = date_str or dt.date.today().isoformat()
    lines = []
    lines.append("---")
    lines.append("tags: [主力行为学, 看板]")
    lines.append("类型: 看板")
    lines.append("状态: 待复核")
    lines.append(f"updated: {date_str}")
    lines.append("source: 主力行为学平台引擎")
    lines.append("---")
    lines.append("")
    lines.append("# 🧭 自选股主力行为学看板")
    lines.append("")
    lines.append(f"> 数据截至 **{date_str}**。点每票进详情页看完整买卖点。")
    lines.append("")
    lines.append("| 代码 | 名称 | 现价 | 涨跌% | 状态 | 三态结论 |")
    lines.append("|------|------|------|-------|------|----------|")
    for a in stocks:
        v = a["verdict"]
        icon = ACTION_ICON.get(v["action"], "🟡")
        lines.append(f"| {a['code']} | {a['name']} | {a['last_close']:.2f} | {a['last_pct']:+.2f}% | {a['state']} | {icon} {v['label']} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    if market:
        lines += market_section(market)
    lines.append("")
    lines.append("> ⚠️ 引擎按主力行为学规则自动生成，非荐股。")
    return "\n".join(lines) + "\n"


def write_output(a: dict, sector: str = "", market: dict = None) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{a['code']}_{a['name']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_stock_page(a, sector, market))
    return path


if __name__ == "__main__":
    print("output dir:", OUTPUT_DIR)
