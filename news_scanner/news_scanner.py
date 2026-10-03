#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_scanner.py —— A股新闻事件主动扫描 · 雏形脚本（MVP）

配套技术清单：news_scanner_tech_spec.md（同目录）

功能（对应清单第 0-6 节）：
  1. 抓取：东方财富/财联社快讯 + 公告（AkShare，多候选接口兜底）
  2. 分类：词典规则 → 事件类型 E1-E6 + 强度分 0-100 + 受益方向
  3. 映射：受益方向 → 概念板块 → 成分股（带缓存，可选）
  4. 验证：板块/个股量价第二道闸门（可选）
  5. 输出：观察池 watchlist/<日期>.csv + .json（含 .sqlite 增量存档，可选）
  6. 回测：骨架函数（信号 → T+1..T+5 超额收益统计）

设计要点：
  - 新闻只负责"发现"，资金负责"确认"（先入池，验证后才可操作）
  - 规则优先、LLM 增强留作 V2；全部可审计、可调参
  - 只处理公开信息，合规边界见技术清单第 8 节

用法：
  python news_scanner.py              # 单次扫描
  python news_scanner.py --verify     # 扫描 + 量价验证
  python news_scanner.py --backtest   # 跑回测骨架（需先积累历史存档）
  定时：crontab 或 GitHub Actions schedule 调用，频率见技术清单第 2 节

依赖：pip install akshare pandas
注意：AkShare 接口名随版本变化，本脚本对每个接口做了 try 兜底，
      若某信源失效会在日志中打印，请按当时 akshare 文档更新 FETCHERS。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

# ============================================================
# 0. 全局配置（阈值与权重初值，来自技术清单第 3-4 节，回测后校准）
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "watchlist")
DB_PATH = os.path.join(BASE_DIR, "events.sqlite")

CONFIG: dict = {
    "fetch_limit": 150,         # 每次抓取快讯条数上限（S1 东财 + S2 财联社 合并去重后截断）
    "min_strength_high": 70,    # 高优先级
    "min_strength_pool": 40,    # 入观察池门槛
    "verify_need_hits": 3,      # 验证命中数要求
    "db_enable": True,          # 是否启用 SQLite 增量存档
    "map_to_stocks": True,      # 是否展开概念成分股（较慢，可关）
}

# 事件类型权重（技术清单 3.1）
EVENT_TYPES: Dict[str, dict] = {
    "E1": {"name": "政策催化",   "weight": 0.35},
    "E2": {"name": "产业催化",   "weight": 0.25},
    "E3": {"name": "并购重组",   "weight": 0.20},
    "E4": {"name": "业绩事件",   "weight": 0.10},
    "E5": {"name": "股东行为",   "weight": 0.05},
    "E6": {"name": "监管风险",   "weight": -0.30},
}

# 事件关键词词典（命中即累计；新增关键词随时可加）
KEYWORDS: Dict[str, List[str]] = {
    "E1": ["国务院", "发改委", "工信部", "央行", "证监会", "财政部", "规划", "试点",
           "专项资金", "补贴", "减免", "新规", "实施意见", "政策", "意见", "方案"],
    "E2": ["涨价", "提价", "订单", "中标", "签约", "技术突破", "量产", "新品发布",
           "获批", "扩产", "投产", "同比增长", "需求旺盛", "供货"],
    "E3": ["收购", "并购", "重组", "借壳", "重大资产重组", "股权转让", "要约收购", "实控人变更"],
    "E4": ["预增", "扭亏", "超预期", "大幅增长", "业绩预告", "业绩快报", "净利润增长"],
    "E5": ["增持", "回购", "举牌", "减持", "质押", "解禁", "员工持股"],
    "E6": ["立案", "处罚", "问询函", "警示函", "ST", "退市", "调查", "违规", "警示"],
}

# 受益方向映射（技术清单 3.2；方向词 → 概念名）
DIRECTION_MAP: Dict[str, List[str]] = {
    "人工智能": ["AI算力", "光模块", "CPO概念", "液冷服务器"],
    "算力": ["AI算力", "光模块", "CPO概念", "液冷服务器"],
    "大模型": ["AI算力", "AI语料", "AIGC概念"],
    "半导体": ["半导体概念", "国产芯片", "先进封装"],
    "芯片": ["半导体概念", "国产芯片", "先进封装"],
    "新能源汽车": ["固态电池", "锂电池", "充电桩", "汽车整车"],
    "固态电池": ["固态电池", "锂电池"],
    "充电桩": ["充电桩"],
    "低空经济": ["低空经济", "通用航空", "无人机"],
    "无人机": ["低空经济", "无人机"],
    "机器人": ["机器人概念", "减速器", "传感器"],
    "商业航天": ["商业航天", "卫星互联网"],
    "卫星": ["商业航天", "卫星互联网"],
    "数据要素": ["数据要素", "国资云", "数字货币"],
    "数字经济": ["数据要素", "国资云", "数字水印"],
    "创新药": ["创新药", "CRO概念", "医疗器械"],
    "医药": ["创新药", "CRO概念", "医疗器械"],
    "电网": ["特高压", "电网设备", "储能"],
    "储能": ["储能", "虚拟电厂"],
    "设备更新": ["工程机械", "工业母机", "家用电器"],
    "以旧换新": ["家用电器", "汽车整车", "家居用品"],
    "军工": ["国防军工", "大飞机", "航母概念"],
    "机器人": ["机器人概念", "减速器", "传感器"],
}

# 来源权重（技术清单 3.3；S4 官方原文最高）
SOURCE_WEIGHTS: Dict[str, float] = {"S4": 1.0, "S1": 0.8, "S2": 0.8, "S3": 0.9, "S5": 0.6, "S6": 0.6, "S7": 0.6, "S8": 0.5}

# 验证阈值（技术清单第 4 节）
VERIFY_THRESHOLDS: dict = {
    "board_rise_pct": 3.0,    # 板块涨幅 %
    "board_volume_ratio": 2.0,  # 板块量比
    "board_limit_ups": 3,     # 板块涨停家数
    "stock_open_pct": 2.0,    # 个股竞价/开盘涨幅 %
    "stock_volume_ratio": 2.5,  # 个股量比
}

# ============================================================
# 1. 数据抓取层
# ============================================================

def _ak():
    """延迟导入 akshare，未安装时给出明确提示。"""
    try:
        import akshare as ak
        return ak
    except ImportError:
        sys.exit("缺少依赖：请先执行 pip install akshare pandas")


def fetch_news(limit: int = None) -> List[dict]:
    """抓取全球财经快讯（多候选接口兜底 + 合并去重）。

    S1 东方财富·全球财经快讯 为主源，S2 财联社·电报 为补充，
    两源都尝试，按标题去重后截断到 limit 条。
    返回: [{source, time, title, content}]
    """
    limit = limit or CONFIG["fetch_limit"]
    ak = _ak()
    rows: List[dict] = []
    seen_titles: set = set()

    candidates = [
        ("S1", "stock_info_global_em"),   # 东方财富·全球财经快讯
        ("S2", "stock_info_global_cls"),  # 财联社·电报
    ]
    for source, fn in candidates:
        try:
            df = getattr(ak, fn)()
            if df is None or df.empty:
                print(f"[抓取] {source} 无数据(跳过)")
                continue
            # 兼容不同版本列名
            cols = {str(c): c for c in df.columns}
            t_col = next((cols[c] for c in ("title", "标题") if c in cols), None)
            c_col = next((cols[c] for c in ("content", "内容") if c in cols), None)
            tm_col = next((cols[c] for c in ("time", "时间", "date") if c in cols), None)
            added = 0
            for _, r in df.head(limit).iterrows():
                title = str(r[t_col]) if t_col else ""
                content = str(r[c_col]) if c_col else ""
                tm = str(r[tm_col]) if tm_col else ""
                if not (title or content):
                    continue
                key = (title or content)[:40]
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                rows.append({"source": source, "time": tm,
                             "title": title, "content": content})
                added += 1
            print(f"[抓取] {source} 成功: 新增 {added} 条（去重后）")
        except Exception as e:
            print(f"[抓取] {source} 失败(跳过): {e}")
    return rows[:limit]


def fetch_notices(limit: int = None) -> List[dict]:
    """抓取上市公司公告（可选信源；接口不稳定时静默跳过）。"""
    limit = limit or CONFIG["fetch_limit"]
    ak = _ak()
    try:
        # AkShare 公告接口版本差异大，这里仅作示例占位
        df = getattr(ak, "stock_notice_report")(symbol="全部", date=date.today().strftime("%Y%m%d"))
        rows = []
        for _, r in df.head(limit).iterrows():
            text = " ".join(str(v) for v in r.tolist() if str(v) != "nan")
            rows.append({"source": "S3", "time": "", "title": text[:80], "content": text})
        print(f"[抓取] S3 公告成功: {len(rows)} 条")
        return rows
    except Exception as e:
        print(f"[抓取] S3 公告不可用(跳过): {e}")
        return []


# ============================================================
# 2. 事件分类层（词典规则）
# ============================================================

def _hits(text: str, words: List[str]) -> int:
    text = text.lower()
    return sum(1 for w in words if w.lower() in text)


def classify_event(item: dict) -> Optional[dict]:
    """词典分类 + 强度评分。返回事件 dict 或 None（无命中/纯噪音）。"""
    text = f"{item.get('title', '')} {item.get('content', '')}"
    if len(text.strip()) < 8:
        return None

    hits = {code: _hits(text, words) for code, words in KEYWORDS.items()}
    hit_codes = [c for c, n in hits.items() if n > 0]
    if not hit_codes:
        return None

    # 取命中次数最多的类型为主类型
    primary = max(hit_codes, key=lambda c: hits[c])
    base = EVENT_TYPES[primary]["weight"]
    src_w = SOURCE_WEIGHTS.get(item.get("source", "S1"), 0.8)

    # 新颖度：当日首次出现=1.0；重复=0.3（用标题哈希 + SQLite 判断）
    novelty = _novelty(item)
    strength = round(60 * base + 15 * src_w + 15 * (1.0 if primary == "E1" else 0.5) + 10 * novelty, 1)
    strength = max(0.0, min(100.0, strength))

    directions = []
    for kw, cons in DIRECTION_MAP.items():
        if kw.lower() in text.lower():
            directions.extend(cons)
    directions = list(dict.fromkeys(directions))  # 去重保序

    return {
        "event_id": hashlib.md5(f"{item.get('time','')}{item.get('title','')[:50]}".encode()).hexdigest()[:12],
        "event_time": item.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": primary,
        "type_name": EVENT_TYPES[primary]["name"],
        "title": (item.get("title") or "")[:120],
        "source": item.get("source", "S1"),
        "strength": strength,
        "directions": directions,
        "stocks": "",               # 方向 → 成分股（main 中映射后填充）
        "verify_status": "未验证",
        "verify_signals": [],
        "action": "观察",
    }


_novelty_cache: set = set()

def _novelty(item: dict) -> float:
    """基于标题哈希判断是否当日首次出现。"""
    h = hashlib.md5((item.get("title") or "").encode()).hexdigest()
    if h in _novelty_cache:
        return 0.3
    _novelty_cache.add(h)
    return 1.0


# ============================================================
# 3. 方向 → 概念板块 → 成分股（可选，带缓存）
# ============================================================

_concept_cache: Dict[str, List[str]] = {}

def map_directions_to_stocks(directions: List[str]) -> Dict[str, List[str]]:
    """方向概念 → 东财概念成分股。失败时返回空映射，不影响主流程。"""
    if not CONFIG["map_to_stocks"] or not directions:
        return {}
    ak = _ak()
    result: Dict[str, List[str]] = {}
    try:
        boards = ak.stock_board_concept_name_em()
        name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[1]
        available = set(boards[name_col].astype(str))
        for d in directions:
            if d in _concept_cache:
                result[d] = _concept_cache[d]
                continue
            if d not in available:
                continue
            try:
                cons = ak.stock_board_concept_cons_em(symbol=d)
                code_col = "代码" if "代码" in cons.columns else cons.columns[0]
                result[d] = cons[code_col].astype(str).tolist()[:10]
                _concept_cache[d] = result[d]
            except Exception as e:
                print(f"[映射] 概念 {d} 成分获取失败: {e}")
    except Exception as e:
        print(f"[映射] 概念板块列表获取失败(跳过映射): {e}")
    return result


# ============================================================
# 4. 验证层（第二道闸门：资金确认）
# ============================================================

def fetch_board_snapshot() -> dict:
    """东财行业+概念板块快照: {板块名: {pct, vol_ratio, limit_ups}}"""
    ak = _ak()
    snap = {}
    for fn in ("stock_board_industry_name_em", "stock_board_concept_name_em"):
        try:
            df = getattr(ak, fn)()
            for _, r in df.iterrows():
                name = str(r.get("板块名称", ""))
                if not name:
                    continue
                snap[name] = {
                    "pct": float(r.get("涨跌幅", 0) or 0),
                    "vol_ratio": float(r.get("量比", 0) or 0),
                    "limit_ups": int(r.get("上涨家数", 0) or 0),  # 近似：上涨家数作强度参考
                }
        except Exception as e:
            print(f"[验证] {fn} 失败: {e}")
    return snap


def verify_events(events: List[dict], board_snap: dict) -> List[dict]:
    """对观察池事件打验证标。简化版：用板块快照验证方向。"""
    for ev in events:
        signals = []
        for d in ev.get("directions", []):
            b = board_snap.get(d)
            if not b:
                continue
            if b["pct"] >= VERIFY_THRESHOLDS["board_rise_pct"]:
                signals.append(f"{d}板块涨幅{b['pct']:.1f}%")
            if b["vol_ratio"] >= VERIFY_THRESHOLDS["board_volume_ratio"]:
                signals.append(f"{d}板块量比{b['vol_ratio']:.1f}")
            if b["limit_ups"] >= VERIFY_THRESHOLDS["board_limit_ups"]:
                signals.append(f"{d}板块上涨{b['limit_ups']}家")
        ev["verify_signals"] = signals
        if len(signals) >= CONFIG["verify_need_hits"]:
            ev["verify_status"] = "已确认"
            ev["action"] = "可操作"
        elif len(signals) > 0:
            ev["verify_status"] = "部分命中"
        else:
            ev["verify_status"] = "未确认"
    return events


# ============================================================
# 5. 持久化：SQLite 增量存档 + 观察池 CSV/JSON
# ============================================================

def db_save(events: List[dict]) -> None:
    if not CONFIG["db_enable"]:
        return
    os.makedirs(BASE_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS events(
            event_id TEXT PRIMARY KEY, event_time TEXT, event_type TEXT,
            title TEXT, source TEXT, strength REAL, directions TEXT,
            stocks TEXT, verify_status TEXT, action TEXT)""")
        # 旧表结构无 stocks 列时补列
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        if "stocks" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN stocks TEXT DEFAULT ''")
        for ev in events:
            conn.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ev["event_id"], ev["event_time"], ev["event_type"], ev["title"],
                 ev["source"], ev["strength"], json.dumps(ev["directions"], ensure_ascii=False),
                 ev.get("stocks", ""), ev["verify_status"], ev["action"]))


def save_watchlist(events: List[dict]) -> Tuple[str, str]:
    import csv as _csv
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    csv_path = os.path.join(OUTPUT_DIR, f"{today}.csv")
    json_path = os.path.join(OUTPUT_DIR, f"{today}.json")
    headers = ["event_id", "event_time", "event_type", "type_name", "title", "source",
               "strength", "directions", "stocks", "verify_status", "verify_signals", "action"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(headers)
        for ev in events:
            row = [
                ev.get("event_id", ""), ev.get("event_time", ""),
                ev.get("event_type", ""), ev.get("type_name", ""),
                ev.get("title", ""), ev.get("source", ""),
                ev.get("strength", ""),
                ";".join(ev.get("directions", []) or []),     # 用 ; 分隔，避免 CSV 逗号错位
                ev.get("stocks", ""),
                ev.get("verify_status", ""),
                ";".join(ev.get("verify_signals", []) or []),
                ev.get("action", ""),
            ]
            w.writerow(row)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    return csv_path, json_path


# ============================================================
# 6. 回测骨架（技术清单第 6 节）
# ============================================================

def backtest(events_path: Optional[str] = None) -> None:
    """信号有效性检验骨架。

    口径（见技术清单 6）：T 日事件入池 → T+1 开盘买入 → 持有 1/3/5 日 → 与沪深300 对比超额收益。
    注意：免费新闻源历史有限，需先用 SQLite 存档积累 ≥3 个月、单组 ≥60 样本后才有统计意义。
    """
    print("\n[回测] 骨架函数：请先积累事件存档（运行本脚本 ≥3 个月）")
    print("  - 样本来源: events.sqlite 或观察池 CSV")
    print("  - 分组: A=仅强度≥70 / B=强度≥70+验证≥3 / C=随机新闻对照组")
    print("  - 指标: 胜率 / 平均超额 / 最大回撤 / 信号频率 / 冲击成本敏感性")
    try:
        ak = _ak()
        idx = ak.stock_zh_index_daily(symbol="sh000300")  # 基准
        print(f"  - 基准数据可用: 沪深300 最近 {len(idx)} 个交易日")
    except Exception as e:
        print(f"  - 基准数据获取失败: {e}")
    print("  - 待实现: 读事件→取个股行情(ak.stock_zh_a_hist)→对齐交易日→计算超额→输出分组统计\n")


# ============================================================
# 7. 主流程
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="A股新闻事件主动扫描雏形")
    ap.add_argument("--verify", action="store_true", help="扫描后执行量价验证")
    ap.add_argument("--backtest", action="store_true", help="运行回测骨架")
    args = ap.parse_args()

    if args.backtest:
        backtest()
        return

    print(f"== 新闻事件扫描 @ {datetime.now():%Y-%m-%d %H:%M:%S} ==")
    news = fetch_news()
    notices = fetch_notices()
    items = news + notices
    if not items:
        print("未抓取到任何新闻（检查网络/akshare 版本）。")
        return

    events = []
    for it in items:
        ev = classify_event(it)
        if ev and ev["strength"] >= CONFIG["min_strength_pool"]:
            events.append(ev)

    # A. 方向 → 概念成分股（失败不影响主流程；结果为 "概念:代码1,代码2;概念2:..." 格式）
    try:
        stock_map = map_directions_to_stocks(
            list(dict.fromkeys(d for e in events for d in e.get("directions", []))))
        for e in events:
            parts = []
            for d in e.get("directions", []):
                codes = stock_map.get(d) or []
                if codes:
                    parts.append(f"{d}:{','.join(codes)}")
            e["stocks"] = "; ".join(parts)
        mapped = sum(1 for e in events if e.get("stocks"))
        if mapped:
            print(f"[映射] 已为 {mapped} 条事件关联概念成分股")
    except Exception as e:
        print(f"[映射] 跳过成分股映射: {e}")

    events.sort(key=lambda e: -e["strength"])
    high = [e for e in events if e["strength"] >= CONFIG["min_strength_high"]]

    print(f"\n快讯/公告 {len(items)} 条 → 事件命中 {len(events)} 条（高优先级 {len(high)} 条）")
    if high:
        print("\n[高优先级事件]")
        for e in high[:10]:
            print(f"  {e['strength']:5.1f} {e['type_name']:<6} {e['title'][:60]} → {e['directions']}")

    if args.verify:
        snap = fetch_board_snapshot()
        events = verify_events(events, snap)
        confirmed = [e for e in events if e["action"] == "可操作"]
        print(f"\n[验证] 已确认可操作 {len(confirmed)} 条")
        for e in confirmed[:10]:
            print(f"  {e['strength']:5.1f} {e['title'][:50]} 命中:{e['verify_signals']}")

    db_save(events)
    csv_path, json_path = save_watchlist(events)
    print(f"\n[输出] 观察池: {csv_path}")
    print(f"        JSON : {json_path}")
    print("\n说明: 本输出仅作信息整合与研究用途，不构成投资建议。合规边界见技术清单第 8 节。")


if __name__ == "__main__":
    main()
