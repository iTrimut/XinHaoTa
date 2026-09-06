# -*- coding: utf-8 -*-
"""
store.py — 轻量配置持久化
用 JSON 保存 watchlist(自选股) 和 brain(技能开关)。供 web 版"增删股票 / 大脑选择"使用。

文件职责：
  - config.json  运行时真实配置(自选/大脑/主题/调度)，含用户真实持仓 → 已 gitignore，网页增删只写这里
  - watchlist.md 仓库内"示例初始清单"：仅当 config.json 无 watchlist 时作为首次启动的默认自选读取；
                 网页增删**不回写**该文件（避免真实自选进 git/公开仓库）
"""
from __future__ import annotations
import os, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE, "config.json")

DEFAULT_BRAIN = {"ma5": True, "dual_mode": True, "spacetime": True, "exclude": True,
                 "leader": True, "rotation": True, "whale": True, "quarter": True,
                 "gain_loss": True, "recency": True, "see_long": True}


def load():
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG, encoding="utf-8"))
        except Exception:
            pass
    return {"watchlist": [], "brain": dict(DEFAULT_BRAIN)}


def save(cfg):
    """原子写 config.json + 保留一份 .bak 备份。先备份上一份再替换，写入半途崩溃不会损坏。"""
    if os.path.exists(CONFIG):
        try:
            import shutil
            shutil.copy(CONFIG, CONFIG + ".bak")
        except Exception:
            pass
    import tempfile
    d = os.path.dirname(CONFIG)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def get_brain():
    return load().get("brain", dict(DEFAULT_BRAIN))


DEFAULT_SCHEDULE = {"enabled": False, "time": "08:45"}


def get_schedule():
    return load().get("schedule", dict(DEFAULT_SCHEDULE))


def set_schedule(sched):
    cfg = load()
    merged = dict(DEFAULT_SCHEDULE)
    if isinstance(sched, dict):
        merged["enabled"] = bool(sched.get("enabled", merged["enabled"]))
        t = str(sched.get("time", merged["time"]))
        # 规范化 HH:MM 并校验范围(0-23时/0-59分)，非法则保留原值
        if len(t) >= 5 and t[2] == ":" and t[:2].isdigit() and t[3:5].isdigit():
            hh, mm = int(t[:2]), int(t[3:5])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                merged["time"] = f"{hh:02d}:{mm:02d}"
    cfg["schedule"] = merged
    save(cfg)
    return merged


DEFAULT_THEME = "a"
VALID_THEMES = ("a", "b", "c", "d")


def get_theme():
    t = load().get("theme", DEFAULT_THEME)
    return t if t in VALID_THEMES else DEFAULT_THEME


def set_theme(theme):
    cfg = load()
    cfg["theme"] = theme if theme in VALID_THEMES else DEFAULT_THEME
    save(cfg)
    return cfg["theme"]


def set_brain(brain):
    cfg = load()
    merged = dict(DEFAULT_BRAIN)
    merged.update({k: bool(v) for k, v in brain.items() if k in DEFAULT_BRAIN})
    cfg["brain"] = merged
    save(cfg)
    return merged


def get_watchlist():
    """从 config.json 读自选股；空则回退 watchlist.md（避免破坏旧配置）。"""
    cfg = load()
    if cfg.get("watchlist"):
        return cfg["watchlist"]
    # 回退: 从 watchlist.md 表格解析
    import re
    wl = []
    path = os.path.join(BASE, "watchlist.md")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2 or not re.fullmatch(r"\d{6}", cells[0]):
                continue
            wl.append({"code": cells[0], "name": cells[1] or "", "sector": cells[2] if len(cells) > 2 else ""})
    return wl


def add_stock(code, name="", sector=""):
    cfg = load()
    wl = get_watchlist()
    code = code.strip()
    if code in [s["code"] for s in wl]:
        return wl, "已存在"
    wl.append({"code": code, "name": name, "sector": sector})
    cfg["watchlist"] = wl
    save(cfg)
    # 只写 config.json（已 gitignore，含用户真实自选）。
    # 不写回 watchlist.md —— 该文件是仓库内的"示例初始清单"，回写会把真实自选带进 git。
    return wl, "已添加"


def remove_stock(code):
    cfg = load()
    wl = get_watchlist()
    new = [s for s in wl if s["code"] != code]
    if len(new) == len(wl):
        return wl, "不存在"
    cfg["watchlist"] = new
    save(cfg)
    return new, "已删除"


def reorder_stock(ordered_codes):
    """按给定代码顺序重排自选股(拖拽排序用)。保留未传入的代码。"""
    cfg = load()
    wl = get_watchlist()
    by_code = {s["code"]: s for s in wl}
    new = []
    for c in ordered_codes:
        if c in by_code:
            new.append(by_code.pop(c))
    # 追加不在列表里的(避免丢失)
    new.extend(by_code.values())
    cfg["watchlist"] = new
    save(cfg)
    return new
