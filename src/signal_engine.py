# -*- coding: utf-8 -*-
"""
signal_engine.py — 信号引擎（主力行为学大脑）

把 [11个主力行为学技能] 中可确定性计算的部分，转成对单只股票的逐项信号 + 三态结论。

设计：忠实于技能原文，不做主观预测；每项信号给出"数值依据"，结论明确三态（买/卖/观望）。

可计算技能 → 引擎规则：
  - ma5-core-strategy       四状态机 (线上持股/倒车接人/至暗时刻/线下持币) + 3%止损
  - dual-mode-buying        倒车接人(右侧) vs 至暗时刻(左侧) 的区分判据
  - spacetime-resonance-timing  空间位(回撤/前低/MA20) + 时间(距顶/底交易日数)
  - exclusion-based-stock-picking 必跌特征(破位/发散/见光死近似) → 剔除标记
  - gain-loss-same-source / recency-bias-overcoming 作为"风险提示"展示，不参与打分
"""
from __future__ import annotations


# ---------- 基础均线/指标 ----------
def ma(values, length, idx):
    """idx(含)往前 length 个的简单均线"""
    if idx + 1 < length:
        return None
    return sum(values[idx - length + 1 : idx + 1]) / length


def _last(closes, n):
    return closes[-n] if len(closes) >= n else None


# ---------- 1. ma5-core-strategy 四状态机 ----------
def state_machine(rows):
    """返回 (state, reason)。state ∈ {④线上持股,①倒车接人,②至暗时刻,③线下持币}"""
    closes = [r["close"] for r in rows]
    n = len(closes)
    i = n - 1
    c = closes[i]
    ma5 = ma(closes, 5, i)
    ma10 = ma(closes, 10, i)
    ma20 = ma(closes, 20, i)
    if ma5 is None or ma20 is None:
        return "数据不足", "K线长度不足，无法计算均线"
    ma5_prev = ma(closes, 5, i - 1)
    # 5日线方向
    ma5_up = (ma5_prev is not None) and (ma5 > ma5_prev)
    ma20_up = ma20 > ma(closes, 20, i - 1) if i >= 20 else None

    # 4个状态判定优先级: 先看趋势方向再细分
    if c > ma5 and ma5 > ma20 and ma5_up:
        # 线上 + 多头排列 + 5日线上行 → ④ 线上持股
        return "④线上持股", f"收盘{c:.2f}>MA5({ma5:.2f})>MA20({ma20:.2f})，5日线上行，趋势健康"
    if c > ma5 and ma5_up and ma5 < ma20:
        # 站上5日线但未修复中期 → ④ 但弱(反弹初期)
        return "④线上持股(弱)", f"收复MA5({ma5:.2f})但仍在MA20({ma20:.2f})下方，属反弹初期"
    if c < ma5 and c < ma20 and not ma5_up:
        # 线下 + 5日线下行 → ③ 线下持币/狩猎
        return "③线下持币", f"收盘{c:.2f}跌破MA5({ma5:.2f})与MA20({ma20:.2f})，5日线下行，线下持币"
    # 关键区分: 回踩5日线不破(盘中触及未破/收盘守住) → ①; 破位超跌 → ②
    # ①需"前面曾有一波多头(近15日内曾收盘站上当日MA20)" + 当前回踩到 ma5 附近
    # 用滚动 MA20 判定：收盘价 vs 该日自己的 MA20，避免"用今日MA20比历史价"把阴跌也误判成多头
    had_uptrend = False
    for j in range(max(0, i - 15), i):  # 不含当日；窗口前数据不足时按能算的天数判定
        ma20_j = ma(closes, 20, j)
        if ma20_j is not None and closes[j] > ma20_j:
            had_uptrend = True
            break
    near_ma5 = abs(c - ma5) / ma5 < 0.03  # 距5日线3%以内
    if near_ma5 and ma20_up and had_uptrend:
        return "①倒车接人(候选)", f"收盘{c:.2f}回踩MA5({ma5:.2f})附近，需开盘半小时确认守住"
    if c < ma5 and (ma20 is not None and ma20_up):
        return "③线下持币", f"跌破MA5({ma5:.2f})但中期(MA20 {ma20:.2f})仍向上，等待收复或确认破位"
    # 至暗时刻: 破位下行 + 恐慌放量 + 距离前低/回撤大
    return "②至暗时刻(候选)", f"收盘{c:.2f}破位下行，观察是否恐慌放量+快速收复5日线"


# ---------- 2. dual-mode-buying 双模式区分 ----------
def dual_mode(rows):
    """返回 (mode, reason)。mode ∈ {倒车接人(右侧), 至暗时刻(左侧), 不适用}"""
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    c = closes[i]
    ma20 = ma(closes, 20, i)
    if ma20 is None:
        return "不适用", "数据不足"
    ma20_up = ma20 > ma(closes, 20, i - 1)
    # 中期上行中回调 → 右侧倒车接人
    if ma20_up and c >= ma20 * 0.95:
        return "倒车接人(右侧)", f"收盘{c:.2f}在MA20({ma20:.2f})上方/附近且中期上行，回调=右侧低吸机会"
    # 破位下行 + 超跌 → 左侧至暗时刻
    if not ma20_up and c < ma20:
        return "至暗时刻(左侧)", f"破位下行且远低于MA20({ma20:.2f})，属左侧极限抄底博弈，须带3%止损"
    return "不适用", "趋势不明朗，两种模式都不适用，观望"


# ---------- 3. spacetime-resonance-timing 时空共振 ----------
def spacetime(rows):
    """返回 (space_ok, time_ok, detail)。空间=到达关键支撑/回撤充分; 时间=自高点调整达到周级别"""
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    c = closes[i]
    # 空间: 回撤深度。找近60日高点
    look = min(60, len(highs))
    recent_high = max(highs[-look:])
    drawdown = (c - recent_high) / recent_high
    # 时间: 距近期高点的交易日数
    if recent_high in highs[-look:]:
        dist = highs[-look:][::-1].index(recent_high)  # 距最近一次高点的位次
        high_idx = i - dist
    else:
        high_idx = i
    days_since_high = i - high_idx
    space_ok = drawdown <= -0.15  # 回撤≥15%视为空间到位(近似, 具体随个股弹性调)
    time_ok = days_since_high >= 5  # 周级别(约5个交易日)调整
    detail = (
        f"近{look}日高点{recent_high:.2f}, 现价{c:.2f}回撤{drawdown*100:.1f}%"
        f"({'空间到位' if space_ok else '空间不足'}), 距高点{days_since_high}个交易日"
        f"({'时间充分' if time_ok else '时间不足'})"
    )
    return (space_ok, time_ok, detail)


# ---------- 4. exclusion-based-stock-picking 排除必跌特征 ----------
def exclusion_flags(rows):
    """返回 list of (flag, reason)。命中即标记为应排除/警惕。"""
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    c = closes[i]
    ma20 = ma(closes, 20, i)
    ma5 = ma(closes, 5, i)
    flags = []
    if ma20 and c < ma20 and ma20 < ma(closes, 20, i - 1):
        flags.append(("破位下行", f"收盘{c:.2f}<MA20({ma20:.2f})且MA20下行，趋势已破坏"))
    if ma5 and c < ma5 * 0.97:
        flags.append(("跌破5日线", f"收盘{c:.2f}<MA5({ma5:.2f})，短线转弱"))
    # 见光死/业绩雷窗口近似: 近期放量滞涨 (观察换手与涨跌背离)
    last = rows[-1]
    if last["to"] > 8 and last["pct"] < 0:
        flags.append(("放量滞涨(出货疑)", f"换手{last['to']:.1f}%却收跌{last['pct']:.1f}%，警惕主力派发"))
    if not flags:
        flags.append(("无排除信号", "未命中破位/跌破5日线/放量滞涨等必跌特征"))
    return flags


# ---------- 5. 3%止损与关键价位 ----------
def stop_price(rows):
    """返回 dict: 触发买入价参考、止损价、关键支撑/压力"""
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    c = closes[i]
    ma5 = ma(closes, 5, i)
    ma20 = ma(closes, 20, i)
    recent_low = min(r["low"] for r in rows[-20:])
    recent_high = max(r["high"] for r in rows[-20:])
    entry_ref = ma5 if ma5 else c
    return {
        "现价": round(c, 2),
        "参考入场(5日线)": round(entry_ref, 2),
        "止损价(-3%)": round(entry_ref * 0.97, 2),
        "近20日支撑": round(recent_low, 2),
        "近20日压力": round(recent_high, 2),
        "距支撑": round((c - recent_low) / recent_low * 100, 1),
    }


# ---------- 汇总单票信号 ----------
def analyze(rows, name="", code="", enabled=None):
    """对单只股票跑全部分析，返回结构化 dict。enabled 为技能开关 dict（brain），缺省全开。"""
    if enabled is None:
        enabled = {"state": True, "mode": True, "spacetime": True, "exclude": True}
    state, state_reason = state_machine(rows)
    mode, mode_reason = dual_mode(rows)
    space_ok, time_ok, space_detail = spacetime(rows)
    flags = exclusion_flags(rows)
    stop = stop_price(rows)
    # 近因/盈亏同源说明 (行为层, 不改变结论)
    last = rows[-1]
    closes = [r["close"] for r in rows]
    i = len(closes) - 1
    ma5 = ma(closes, 5, i)
    pct_str = " / ".join("%+.1f%%" % r["pct"] for r in rows[-5:])
    recency_note = (
        f"近5日涨跌: {pct_str}。"
        f"别被最近1-2天绑架——按5日线({ma5:.2f})这一客观锚执行，克服近因效应。"
        if ma5 else ""
    )
    return {
        "code": code, "name": name, "last_date": last["date"], "last_close": last["close"],
        "last_pct": last["pct"], "last_turnover": last["to"],
        "ma5": ma(closes, 5, i), "ma10": ma(closes, 10, i), "ma20": ma(closes, 20, i),
        "state": state, "state_reason": state_reason,
        "mode": mode, "mode_reason": mode_reason,
        "space_ok": space_ok, "time_ok": time_ok, "space_detail": space_detail,
        "flags": flags, "stop": stop, "recency_note": recency_note,
        "enabled": enabled,
        "verdict": verdict(state, mode, flags, space_ok, time_ok, enabled, space_detail),
    }


# ---------- 三态结论 ----------
def verdict(state, mode, flags, space_ok, time_ok, enabled=None, space_detail=""):
    """综合出 买/卖/观望 + 一/两句话理由 (面向普通投资者可读)。
    enabled 为 brain 开关；某控制位关掉则忽略对应信号，避免该技能主导结论。"""
    if enabled is None:
        enabled = {"state": True, "mode": True, "spacetime": True, "exclude": True}
    has_exclude = any(f[0] != "无排除信号" for f in flags)
    # 卖：state(ma5) 为主导；弱走/破位线下 → 卖
    if enabled.get("state", True) and state.startswith("③"):
        return {
            "action": "sell", "label": "卖出/离场",
            "reason": f"已跌破5日线且中期转弱，线下持币。{flags[0][1] if flags else ''}"
        }
    # 排除法独立生效：即使 state 非③，只要命中"破位/跌破5日线/放量滞涨"等必跌特征 → 降级卖出
    if enabled.get("exclude", True) and has_exclude and state not in ("②至暗时刻(候选)",):
        hit = next((f for f in flags if f[0] != "无排除信号"), None)
        return {
            "action": "sell", "label": "卖出/离场(排除法)",
            "reason": f"命中必跌特征：{hit[0]}—{hit[1]}。排除法优先规避，卖出/离场。"
        }
    # 至暗时刻 → 观望 (需 state 与 mode 生效)
    if enabled.get("state", True) and enabled.get("mode", True) and state.startswith("②"):
        return {
            "action": "watch", "label": "观望/不接飞刀",
            "reason": "破位下行，尚未出现恐慌放量+快速收复5日线的反转确认，左侧不盲抄。设3%止损再做。"
        }
    # 倒车接人 / 站回5日线 → 需 state + mode + spacetime 三全才给买
    if enabled.get("state", True) and (state.startswith("①") or state.startswith("④线上持股(弱)")):
        if enabled.get("spacetime", True) and space_ok and time_ok:
            return {
                "action": "buy", "label": "买入/低吸",
                "reason": f"回踩5日线企稳且时空共振(空间到位+时间充分)。{space_detail}。开盘半小时守住5日线飘红则进，设3%止损。"
            }
        if enabled.get("spacetime", True):
            return {
                "action": "watch", "label": "观望/等确认",
                "reason": f"回踩到5日线附近但时空只满足之一。{space_detail}。等守住5日线+空间时间都到位再低吸。"
            }
        return {
            "action": "watch", "label": "观望",
            "reason": "已到5日线附近但未启用时空共振，保守观望。"
        }
    # ④线上持股(强) → 持股待涨 (需 state)
    if enabled.get("state", True) and state.startswith("④"):
        return {
            "action": "hold", "label": "持股待涨",
            "reason": "线上持股，5日线不破继续持有；小高点后回踩5日线=做差价机会。跌破5日线再走。"
        }
    return {"action": "watch", "label": "观望",
            "reason": f"主力行为学信号未开启足够维度(当前enable={ {k:v for k,v in enabled.items()} })，暂不明确。"}


if __name__ == "__main__":
    import data_fetch
    rows = data_fetch.fetch_kline("605358", limit=260)
    r = analyze(rows, "立昂微", "605358")
    import json
    print(json.dumps(r, ensure_ascii=False, indent=1))
