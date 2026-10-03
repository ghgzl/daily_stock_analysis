#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_scanner/send_email.py —— 每日观察池邮件推送（标准库 SMTP，零第三方依赖）

用途（GitHub Actions 定时调用）：
  把当日事件简报 + 观察池摘要 + 缠论报告链接发到指定邮箱。

配置（GitHub Actions Secrets / 环境变量）：
  SMTP_HOST     e.g. smtp.qq.com
  SMTP_PORT     e.g. 465
  SMTP_USER     发件邮箱账号
  SMTP_PASS     SMTP 授权码（QQ 邮箱需在设置中开启 SMTP 并生成授权码，不是登录密码）
  MAIL_TO       收件邮箱（可逗号分隔多个）
  可选: MAIL_SUBJECT 主题前缀

用法：
  python send_email.py                          # 自动找当日最新 watchlist
  python send_email.py --watchlist 路径.csv     # 指定文件
  python send_email.py --dry-run                # 只打印邮件内容不发信（本地调试）
"""

from __future__ import annotations

import argparse
import smtplib
import sys
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WATCH_DIR = BASE_DIR / "watchlist"

REPORT_URL = "https://github.com/ghgzl/daily_stock_analysis/blob/main/chan_report.md"
RADAR_URL = "https://appapppy-f4cxuoxhwyuegj4bjw9fqu.streamlit.app/"  # 你的 chan.py 版网页，可按需替换


def latest_watchlist() -> Path | None:
    if not WATCH_DIR.exists():
        return None
    files = sorted(WATCH_DIR.glob("*.csv"))
    return files[-1] if files else None


def build_subject() -> str:
    return f"[news_scanner] 当日事件简报 {date.today():%m-%d}"


def build_body(watch: Path) -> str:
    """纯文本邮件正文：Top 事件 + 可操作名单 + 链接。"""
    lines = []
    lines.append(f"# 新闻事件扫描简报 {date.today():%Y-%m-%d}")
    lines.append("=" * 40)
    try:
        import csv
        with open(watch, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        rows = []
    if not rows:
        lines.append("今日无高优先级事件（或为非交易日）。")
    else:
        top = sorted(rows, key=lambda r: -float(r.get("strength", 0)))[:10]
        lines.append(f"\nTop {len(top)} 事件（按强度）:")
        for r in top:
            lines.append(
                f"  [{r.get('strength','?')}] {r.get('event_type','')} {r.get('title','')[:50]}"
                f" | 方向: {r.get('directions','')[:60]} | 股票: {r.get('stocks','')[:80] or '—'}"
                f" | 验证: {r.get('verify_status','')}"
            )
        confirm = [r for r in rows if r.get("action") == "可操作"]
        lines.append(f"\n可操作名单（强度+资金验证通过）: {len(confirm)} 条")
        for r in confirm:
            lines.append(f"  - {r.get('title','')[:60]} → 股票: {r.get('stocks','')[:80] or '—'} | {r.get('verify_signals','')[:80]}")

    lines.append("\n" + "=" * 40)
    lines.append(f"缠论分析报告: {REPORT_URL}")
    lines.append(f"交互网页: {RADAR_URL}")
    lines.append("\n免责声明: 本邮件由公开信息自动整合生成，仅供研究参考，不构成投资建议。")
    return "\n".join(lines)


def send(subject: str, body: str, attach: Path | None) -> None:
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "465"))
    user = _env("SMTP_USER")
    pwd = _env("SMTP_PASS")
    to = _env("MAIL_TO")

    msg = MIMEMultipart()
    msg["From"] = Header(f"news_scanner <{user}>")
    msg["To"] = Header(to)
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if attach and attach.exists():
        part = MIMEApplication(attach.read_bytes(), _subtype="csv")
        part.add_header("Content-Disposition", "attachment", filename=attach.name)
        msg.attach(part)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    print(f"[邮件] 已发送到 {to}（附件: {attach.name if attach and attach.exists() else '无'}）")


def _env(k: str, default: str = "") -> str:
    import os
    v = os.environ.get(k, "")
    if not v and default:
        return default
    if not v and not default:
        print(f"[邮件] 缺少环境变量 {k}，请检查 GitHub Secrets 配置。")
        sys.exit(2)
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description="发送每日观察池邮件")
    ap.add_argument("--watchlist", default=None, help="指定 CSV；默认取当日最新")
    ap.add_argument("--dry-run", action="store_true", help="只打印内容不发送")
    args = ap.parse_args()

    watch = Path(args.watchlist) if args.watchlist else latest_watchlist()
    if watch is None or not watch.exists():
        print("[邮件] 未找到观察池 CSV，跳过发送。")
        return

    subject = build_subject()
    body = build_body(watch)
    if args.dry_run:
        print("== 邮件内容预览 ==")
        print(body)
        return
    send(subject, body, watch)


if __name__ == "__main__":
    main()
