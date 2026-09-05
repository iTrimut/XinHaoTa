# -*- coding: utf-8 -*-
"""
brain.py — 大脑（技能开关）配置层

定义主力行为学 11 个技能的元数据 + 分组 + 默认启用状态，供前端"大脑选择"面板切换。
一个技能禁用后，它对三态结论(verdict)的贡献会真正被排除（不是装饰）。

分组：
  - A层·个股信号：直接影响单票 verdict（开关会改变结论）
  - B层·市场环境：只影响市场环境区块（开关显示/隐藏对应技能输出）
  - 行为层·提示：只做人性防线展示，不改结论（toggle 只控显示）
"""
from __future__ import annotations

# 技能元数据。control = 该技能控制 verdict 的哪一环
SKILLS = [
    # ---- A层 · 个股信号（开关会真正改变结论）----
    {"id": "ma5", "name": "5天线战法", "group": "A层·个股信号",
     "desc": "线上持股线下持币：四状态机 + 3%止损", "control": "state", "default": True},
    {"id": "dual_mode", "name": "双模式买入", "group": "A层·个股信号",
     "desc": "倒车接人(右侧) vs 至暗时刻(左侧) 的区分", "control": "mode", "default": True},
    {"id": "spacetime", "name": "时空共振", "group": "A层·个股信号",
     "desc": "空间到位 + 时间充分 双验证，买点门槛", "control": "spacetime", "default": True},
    {"id": "exclude", "name": "排除法", "group": "A层·个股信号",
     "desc": "剔除破位/跌破5日线/放量滞涨等必跌特征", "control": "exclude", "default": True},
    # ---- B层 · 市场环境（开关显示/隐藏该技能输出）----
    {"id": "leader", "name": "龙头定高度", "group": "B层·市场环境",
     "desc": "用龙头位置标定市场真实高度，龙头破位=行情结束", "control": "market", "default": True},
    {"id": "rotation", "name": "板块轮动", "group": "B层·市场环境",
     "desc": "一条裤子轮流穿：识别谁涨完该休、谁该轮到", "control": "market", "default": True},
    {"id": "whale", "name": "鲸落万物生", "group": "B层·市场环境",
     "desc": "强势补跌→资金释放给弱者，万物'弹'非'生'", "control": "market", "default": True},
    {"id": "quarter", "name": "后四分之一", "group": "B层·市场环境",
     "desc": "马拉松淘汰，锁定还在强势行列的存活者", "control": "market", "default": True},
    # ---- 行为层 · 提示（toggle 只控显示，不改结论）----
    {"id": "gain_loss", "name": "盈亏同源", "group": "行为层·提示",
     "desc": "接受一个缺陷：回撤(卖飞)或踏空，择一", "control": "display", "default": True},
    {"id": "recency", "name": "近因效应克服", "group": "行为层·提示",
     "desc": "以客观技术锚对抗涨了看涨跌了看跌", "control": "display", "default": True},
    {"id": "see_long", "name": "看长做短", "group": "行为层·提示",
     "desc": "熟悉票上反复进出做差价，长期投资≠长期持有", "control": "display", "default": True},
]

DEFAULT_ENABLED = {s["id"]: s["default"] for s in SKILLS}


def skill_catalog():
    """给前端的大脑选择面板：技能列表 + 分组的默认启用状态。"""
    return {"skills": SKILLS, "default": DEFAULT_ENABLED}


def group_name(id_):
    for s in SKILLS:
        if s["id"] == id_:
            return s["group"]
    return ""


def is_verdict_skill(id_):
    """是否属于 A层(真正影响结论)的技能。"""
    return any(s["id"] == id_ and s["control"] != "market" and s["control"] != "display" for s in SKILLS)


def order_groups():
    """面板分组顺序：A层 → B层 → 行为层。"""
    return ["A层·个股信号", "B层·市场环境", "行为层·提示"]


def default_controls():
    """返回各控制位默认是否生效(%)，供 verdict 逻辑使用。"""
    return {
        "state": DEFAULT_ENABLED.get("ma5", True),
        "mode": DEFAULT_ENABLED.get("dual_mode", True),
        "spacetime": DEFAULT_ENABLED.get("spacetime", True),
        "exclude": DEFAULT_ENABLED.get("exclude", True),
    }
