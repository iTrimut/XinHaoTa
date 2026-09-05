# -*- coding: utf-8 -*-
"""
context_engine.py — 市场上下文引擎（主力行为学 B 层）

把需要"市场/板块/龙头"维度的 4 个技能转成确定性计算（对 A 层个股信号做环境加持）：

  - leader-defines-height        龙头定高度：用龙头/大盘集群位置代替失真指数，标定市场真实高度与风险
  - sector-rotation-framework    板块轮动：识别对立阵营，判断"谁在穿裤子"（刚涨完要休）vs"谁在等裤子"（该轮到）
  - whale-fall-rebirth           鲸落万物弹：强板块补跌→资金释放给弱者，弱者=反弹窗口
  - last-quarter-stock-screening 后四分之一：动态淘汰，锁定还在强势行列的"存活者"

设计原则：优先用**已有数据确定性计算**；板块指数等外部数据缺失时**优雅降级**（输出"数据不足"），
绝不臆造。它产出的是"市场/板块环境"这张上下文，叠加到单票结论上。
"""
from __future__ import annotations


# ---------- 基准指标 ----------
def _ma(values, length, idx):
    if idx + 1 < length:
        return None
    return sum(values[idx - length + 1 : idx + 1]) / length


def _rel_strength(rows):
    """个股相对强度：现价相对近60日高低区间的位置 [0,1]，越高越强。空数据返回 0.5。"""
    if not rows:
        return 0.5
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    look = min(60, len(closes))
    hi = max(r["high"] for r in rows[-look:])
    lo = min(r["low"] for r in rows[-look:])
    c = closes[i]
    if hi == lo:
        return 0.5
    # clamp 到 [0,1]（防除权/异常数据超界）
    return max(0.0, min(1.0, (c - lo) / (hi - lo)))


def _sector_of(a, sector_map):
    return sector_map.get(a["code"], "")


# ---------- 1. leader-defines-height 龙头定高度 ----------
def market_height(analyses, sector_map, index_rows=None):
    """用龙头集群(最强者)位置标定真实高度。返回 dict。index_rows 为可选的真实参照系(深指/中证1000)。
    核心信号: 龙头破位=行情结束。
    """
    if not analyses:
        return {"available": False, "note": "无分析样本"}
    # 龙头 = 趋势仍向上(在MA5上 + MA20上 + 中期向上)的旗舰；若无任何票同时满足，
    #          则取相对最健康者并明确标记"无真龙头"，此时市场整体转为线下持币/风险区。
    def _true_leader(a):
        # 需自带涨跌数据; 缺乏则退化为 None
        ma5 = a.get("ma5"); ma20 = a.get("ma20")
        if ma5 is None or ma20 is None:
            return False
        above5 = a["last_close"] >= ma5
        above20 = a["last_close"] >= ma20
        ma20_up = "_rows" in a and _ma([r["close"] for r in a["_rows"]], 20, len(a["_rows"]) - 1) > \
                  _ma([r["close"] for r in a["_rows"]], 20, len(a["_rows"]) - 2)
        return above5 and above20 and ma20_up

    true_candidates = [a for a in analyses if _true_leader(a)]
    if true_candidates:
        true_candidates.sort(key=lambda a: -(_rel_strength(a["_rows"]) if "_rows" in a else 0.5))
        leader = true_candidates[0]
        leader_found = True
        leader_broken = False
    else:
        # 无真龙头 → 取相对最健康(回撤最浅/最接近MA20)者作为"参考领涨"，标记重风险
        scored = sorted(analyses, key=lambda a: -(_rel_strength(a["_rows"]) if "_rows" in a else 0.5))
        leader = scored[0]
        leader_found = False
        leader_broken = True
    leader_rs = _rel_strength(leader["_rows"]) if "_rows" in leader else 0.5
    # 参照系(可选)
    index_note = ""
    if index_rows:
        idx_closes = [r["close"] for r in index_rows]
        i = len(idx_closes) - 1
        idx_ma20 = _ma(idx_closes, 20, i)
        idx_pos = "上方" if (idx_ma20 and idx_closes[i] >= idx_ma20) else "下方"
        index_note = f"真实参照系(深指/中证1000)在MA20{idx_pos}，给市场定高度而非看失真指数"
    return {
        "available": True,
        "leader": leader["name"], "leader_code": leader["code"],
        "leader_rs": round(leader_rs, 2), "leader_above20": (leader["last_close"] >= leader.get("ma20", 0)) if leader.get("ma20") else None,
        "leader_broken": leader_broken, "leader_found": leader_found,
        "leaders": [a["name"] for a in (true_candidates or [leader])[: min(3, len(true_candidates or [leader]))]],
        "note": (
            ("龙为旗舰未破=行情仍在，按既定策略操作。" if leader_found else
             "⚠️ 样本均破位/在均线下，无真龙头=市场整体转弱，降低仓位、多看少动，别当'普涨'做。")
            + index_note
        ),
        "heavy_risk": not leader_found,
    }


# ---------- 2. sector-rotation-framework 板块轮动(一条裤子轮流穿) ----------
def sector_rotation(analyses, sector_map):
    """按板块聚合相对强度，识别"谁穿裤子(刚涨完要休)"vs"谁等裤子(该轮到)"。返回 dict。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for a in analyses:
        sec = _sector_of(a, sector_map)
        if sec:
            buckets[sec].append(_rel_strength(a["_rows"]) if "_rows" in a else 0.5)
    if len(buckets) < 2:
        return {"available": False, "note": "板块数<2，无法对比跷跷板。请在 watchlist.md 给股票标注板块。"}
    # 每板块平均相对强度 + 近3日动量
    rows_out = []
    for sec, rss in buckets.items():
        avg = sum(rss) / len(rss)
        rows_out.append((sec, avg, len(rss)))
    rows_out.sort(key=lambda x: x[1])
    weakest, strongest = rows_out[0], rows_out[-1]
    return {
        "available": True,
        "ranking": [{"sector": s, "strength": round(avg, 2), "count": n} for s, avg, n in rows_out],
        "wearing_pants": strongest[0],   # 刚涨完/最热 → 即将休息
        "waiting_pants": weakest[0],      # 刚跌完/最冷 → 即将轮到低吸
        "note": f"等[{weakest[0]}]回踩企稳再低吸，别追刚涨完的[{strongest[0]}]。",
    }


# ---------- 3. whale-fall-rebirth 鲸落万物弹 ----------
def whale_fall(analyses, sector_map):
    """最强板块开始补跌 = 资金释放 → 弱者反弹窗口。返回 dict。
    判据: 最强板块相对强度近期转弱(其龙头跌破5日线) 且 存在明显弱势板块。
    """
    rot = sector_rotation(analyses, sector_map)
    if not rot.get("available"):
        return {"available": False, "note": rot.get("note", "板块数据不足")}
    # 找最强板块里的龙头，看其最近的5日线状态
    strongest = rot["wearing_pants"]
    strong_stocks = [a for a in analyses if _sector_of(a, sector_map) == strongest]
    if strong_stocks:
        s = strong_stocks[0]
        ma5 = s.get("ma5")
        strong_leader_broke = (ma5 and s["last_close"] < ma5)
    else:
        strong_leader_broke = False
    weakest = rot["waiting_pants"]
    return {
        "available": True,
        "strong_sector": strongest,
        "weak_sector": weakest,
        "strong_leader_broke": strong_leader_broke,
        "whale_falling": strong_leader_broke,
        "note": (
            f"最强板块[{strongest}]龙头{'跌破5日线=鲸落(资金释放)，弱者' if strong_leader_broke else '未破位=资金未释放，弱者'}"
            f"[{weakest}]机会{'打开' if strong_leader_broke else '未到'}。注意万物'弹'非'生'，多数只是反弹不是反转。"
        ),
    }


# ---------- 4. last-quarter-stock-screening 后四分之一 ----------
def last_quarter(analyses):
    """动态淘汰：按相对强度排序，标出"存活者(头部)"与"掉队者(尾部)"。返回 dict。"""
    if len(analyses) < 4:
        return {"available": False, "note": "样本<4，后四分之一筛选意义有限"}
    scored = sorted(analyses, key=lambda a: -(_rel_strength(a["_rows"]) if "_rows" in a else 0.5))
    n = len(scored)
    survivors = [a["name"] for a in scored[: max(1, n // 4)]]        # 头部1/4
    laggards = [a["name"] for a in scored[-max(1, n // 4):]]         # 尾部1/4
    # 存活者是否仍在强势: 相对强度>0.5
    top_ok = any((_rel_strength(a["_rows"]) if "_rows" in a else 0.5) > 0.5 for a in scored[: max(1, n // 4)])
    return {
        "available": True,
        "survivors": survivors, "laggards": laggards,
        "top_ok": top_ok,
        "note": f"马拉松淘汰：存活者={', '.join(survivors)}，掉队者={', '.join(laggards)}。"
                f"{'头部仍强，聚焦存活者' if top_ok else '连头部都在衰减，谨慎'}。",
    }


# ---------- 汇总市场上下文 ----------
def analyze_context(analyses, sector_map, index_rows=None, enabled=None):
    """跑 4 个 B 层技能，返回 market_context dict。enabled 为 brain 开关(可选)，被禁用的技能返回 disabled=True。"""
    if enabled is None:
        enabled = {"leader": True, "rotation": True, "whale": True, "quarter": True}
    def _wrap(ok, payload):
        if not ok:
            payload["disabled"] = True
        return payload
    return {
        "height": _wrap(enabled.get("leader", True), market_height(analyses, sector_map, index_rows)),
        "rotation": _wrap(enabled.get("rotation", True), sector_rotation(analyses, sector_map)),
        "whale": _wrap(enabled.get("whale", True), whale_fall(analyses, sector_map)),
        "quarter": _wrap(enabled.get("quarter", True), last_quarter(analyses)),
    }
