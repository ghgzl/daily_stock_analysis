#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apps/news_radar/app.py —— Streamlit 新闻事件雷达（第三个交互网页）

数据流（零本地部署）：
  GitHub Actions 定时跑 news_scanner → watchlist/<日期>.csv 提交回仓库
  → 本 app 只读仓库内文件做展示（轻量，适配 Streamlit Cloud 免费版 1GB 内存）

页面：
  - 事件雷达：Top 事件卡片（强度/类型/方向/验证状态）
  - 观察池：表格 + 可操作名单筛选
  - 缠论报告：解析 chan_report.md + 展示 chan_img K线图
  - 说明：合规声明

部署（Streamlit Cloud）：
  1. 本文件位于 apps/news_radar/app.py，入口指向它（Streamlit Cloud 里设置 Main file path）
  2. requirements.txt 见同目录
  3. 推送 main 分支 → 自动部署
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parents[2]          # 仓库根
WATCH_DIR = BASE_DIR / "news_scanner" / "watchlist"      # news_scanner 输出
CHAN_REPORT = BASE_DIR / "chan_report.md"                # 缠论报告
CHAN_IMG_DIR = BASE_DIR / "chan_img"                     # K线图

st.set_page_config(page_title="新闻事件雷达 · news_scanner", layout="wide", initial_sidebar_state="expanded")

# ---------------- 数据读取 ----------------

@st.cache_data(ttl=300, show_spinner=False)
def list_watch_dates() -> list:
    """watchlist 目录下已有观察池日期（倒序）。"""
    if not WATCH_DIR.exists():
        return []
    return sorted([p.stem for p in WATCH_DIR.glob("*.csv")], reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_watchlist(date_str: str) -> pd.DataFrame:
    p = WATCH_DIR / f"{date_str}.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, encoding="utf-8")


def load_chan_sections() -> list:
    """解析 chan_report.md → [{title, lines, images}]。"""
    if not CHAN_REPORT.exists():
        return []
    text = CHAN_REPORT.read_text(encoding="utf-8")
    parts = re.split(r"\n(?=## )", text)
    sections = []
    for part in parts:
        lines = [ln.strip() for ln in part.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0].lstrip("#").strip()
        images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", part)
        body = [ln for ln in lines[1:] if not ln.startswith("![") and not ln.startswith(">")]
        sections.append({"title": title, "body": body, "images": images})
    return sections


# ---------------- 侧边栏 ----------------

with st.sidebar:
    st.title("📡 新闻事件雷达")
    dates = list_watch_dates()
    if not dates:
        st.warning("尚未生成观察池。请先在 GitHub Actions 跑通 news_scanner 或查看说明页。")
        sel_date = None
    else:
        sel_date = st.selectbox("观察池日期", dates, index=0)

    min_strength = st.slider("最低事件强度", 0, 100, 40, 5)
    type_filter = st.multiselect(
        "事件类型", ["E1 政策催化", "E2 产业催化", "E3 并购重组", "E4 业绩事件", "E5 股东行为", "E6 监管风险"],
        default=[])

    st.caption("数据来源：news_scanner 主动扫描（公开信息）")
    st.caption("合规：本页仅作信息整合与研究用途，不构成投资建议。")

# ---------------- 主区 ----------------

tab_radar, tab_pool, tab_chan, tab_about = st.tabs(["事件雷达", "观察池", "缠论报告", "说明"])

with tab_radar:
    st.subheader("当日高优先级事件")
    if not sel_date:
        st.info("暂无数据。部署后先跑一次 GitHub Actions 生成观察池。")
    else:
        df = load_watchlist(sel_date)
        if df.empty:
            st.info(f"{sel_date} 没有事件（可能为非交易日或当天无强信号）。")
        else:
            df = df[df["strength"] >= min_strength]
            if type_filter:
                df = df[df["event_type"].isin([t.split(" ")[0] for t in type_filter])]
            df = df.sort_values("strength", ascending=False)
            for _, row in df.head(12).iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([5, 2, 2])
                    c1.markdown(f"**{row['title']}**")
                    c2.markdown(f"`{row['event_type']} {row['type_name']}`")
                    c3.markdown(f"**强度 {row['strength']:.0f}**")
                    st.progress(min(100, float(row["strength"])) / 100)
                    st.caption(f"来源 {row['source']} · 时间 {row['event_time']} · 验证: {row['verify_status']} · 方向: {row['directions']}")

with tab_pool:
    st.subheader("观察池明细")
    if sel_date:
        df = load_watchlist(sel_date)
        if not df.empty:
            st.dataframe(
                df[["strength", "event_type", "type_name", "title", "source", "directions",
                    "stocks", "verify_status", "action"]].sort_values("strength", ascending=False),
                use_container_width=True, hide_index=True)
            confirmed = df[df["action"] == "可操作"]
            if not confirmed.empty:
                st.success(f"今日可操作名单（强度+资金验证通过）：{len(confirmed)} 条")
                st.dataframe(confirmed[["title", "directions", "stocks", "verify_signals", "strength"]],
                             use_container_width=True, hide_index=True)
            else:
                st.info("今日无可操作名单（无事件通过资金验证闸门）。")

with tab_chan:
    st.subheader("缠论分析报告（chan_report.md）")
    sections = load_chan_sections()
    if not sections:
        st.info("仓库中暂无 chan_report.md（待 GitHub Actions 首次生成）。")
    else:
        for sec in sections:
            with st.expander(sec["title"], expanded=False):
                for ln in sec["body"][:8]:
                    st.markdown(f"- {ln}")
                for img in sec["images"]:
                    img_path = BASE_DIR / img
                    if img_path.exists():
                        st.image(str(img_path), caption=img)

with tab_about:
    st.subheader("系统说明")
    st.markdown(
        """
- **定位**：news_scanner 是 daily_stock_analysis 的 companion 模块（全市场事件扫描），本页只做展示。
- **数据流**：GitHub Actions 定时扫描 → `news_scanner/watchlist/<日期>.csv` 提交回仓库 → 本页读取。
- **验证闸门**：新闻只负责发现；板块涨幅≥3%、量比≥2、涨停≥3 家等资金信号确认后才标记"可操作"。
- **LLM 增强（V2）**：当前为词典规则版（可审计、零费用）；后续可切换大模型事件抽取。
- **合规**：仅使用公开信息，不构成投资建议；不接触内幕信息、不传播未公开信息。
        """
    )
    st.caption(f"报告引用：{CHAN_REPORT} · 观察池目录：{WATCH_DIR}")
