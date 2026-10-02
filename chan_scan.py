# -*- coding: utf-8 -*-
"""
chan.py 三类买点扫描（A股，默认中证500）
判定口径：该股【最新一个买卖点】为第三类买点（3a/3b 且 is_buy=True）→ 命中
引擎：chan.py（本仓库 chanlib/ vendored 源码）+ chan_core.py（数据/清洗/封装）

用法：
  云端(GitHub Actions)：workflow 注入 EMAIL_* 环境变量后  python chan_scan.py
  本机(Windows)：先配置 scan.env（EMAIL_*），再 python chan_scan.py 或双击 start_scan.bat

可选环境变量：
  SCAN_UNIVERSE  股票池：cs500(默认，中证500) / hs300(沪深300) / custom:000725,600519
  SCAN_START     数据起始日，默认 2004-01-01（数据充足，三买更可靠）
  SCAN_END       结束日，留空=最新交易日
  SCAN_LIMIT     调试用：只跑前 N 只（0=全部）
"""
import os
import sys
import time
import random
from datetime import datetime

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 保证 chan_core / chanlib 可导入
from chan_core import get_bars_df, run_chan, ctime_str

# 本机支持 scan.env 配置邮件（云端用系统环境变量）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan.env"))
except Exception:
    pass

# ====================== 【可修改参数】======================
SCAN_UNIVERSE = os.getenv("SCAN_UNIVERSE", "cs500")   # cs500 / hs300 / custom:代码,代码
SCAN_START = os.getenv("SCAN_START", "2004-01-01")    # 数据起始
SCAN_END = os.getenv("SCAN_END", "") or datetime.now().strftime("%Y-%m-%d")
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "0"))        # 0=全部
SLEEP_MIN, SLEEP_MAX = 0.1, 0.4                       # 每只股票随机小延迟，防限流
# =========================================================


def load_universe():
    """返回 [(代码, 名称), ...]；中证500/沪深300 用 akshare 拉成分股，custom 用逗号代码
    兜底：akshare 全部失败时读仓库内置清单（cs500.csv / hs300.csv），保证 GitHub Actions 海外可跑"""
    import akshare as ak
    if SCAN_UNIVERSE.startswith("custom:"):
        codes = [c.strip() for c in SCAN_UNIVERSE.split(":", 1)[1].split(",") if c.strip()]
        return [(c, c) for c in codes]
    symbol = "000905" if SCAN_UNIVERSE == "cs500" else "000300"
    label = "中证500" if SCAN_UNIVERSE == "cs500" else "沪深300"
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cs500.csv" if SCAN_UNIVERSE == "cs500" else "hs300.csv")

    # 依次尝试 akshare 接口（csindex 官网 → 东财 → 旧接口）
    for fn in ("index_stock_cons_csindex", "index_stock_cons_em", "index_stock_cons"):
        try:
            df = getattr(ak, fn)(symbol=symbol)
            if df is None or len(df) == 0:
                print(f"[股票池] 接口 {fn} 返回空，尝试下一个…")
                continue
            code_col = next((c for c in ("成分券代码", "代码", "code", "cons_code") if c in df.columns), None)
            name_col = next((c for c in ("成分券名称", "名称", "name", "cons_name") if c in df.columns), None)
            if code_col is None:
                print(f"[股票池] 接口 {fn} 列名无法识别（{list(df.columns)[:6]}），尝试下一个…")
                continue
            name_col = name_col or code_col
            codes = [str(x).zfill(6) for x in df[code_col].tolist()]
            names = [str(x) for x in df[name_col].tolist()]
            items = list(zip(codes, names))
            print(f"[股票池] {label} {len(items)} 只（akshare 接口 {fn}）")
            return items
        except Exception as e:
            print(f"[股票池] 接口 {fn} 失败：{type(e).__name__}: {str(e)[:100]}，尝试下一个…")

    # 兜底：仓库内置清单（离线可靠，海外 runner 也可用）
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        items = []
        for r in rows:
            code = str(r.get("成分券代码") or r.get("代码") or r.get("code") or "").zfill(6)
            name = str(r.get("成分券名称") or r.get("名称") or r.get("name") or code)
            if code and code != "00000":
                items.append((code, name))
        print(f"[股票池] {label} {len(items)} 只（使用仓库内置清单 {os.path.basename(csv_path)}）")
        return items
    raise RuntimeError(f"无法获取{label}成分股清单（akshare 接口全部失败且无内置清单）")


def is_third_buy(bsp):
    """最新买卖点是否为第三类买点：is_buy 且类型含 3a 或 3b"""
    if bsp is None or not bsp.is_buy:
        return False
    return any(x.value in ("3a", "3b") for x in bsp.type)


def send_email(subject, markdown_text):
    """把扫描结果发到 EMAIL_RECEIVERS（复用 chan_analysis.py 的 SMTP 逻辑）"""
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr

    sender = os.getenv("EMAIL_SENDER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    receivers = os.getenv("EMAIL_RECEIVERS", "")
    if not (sender and password and receivers):
        print("⚠️ 未配置 EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVERS，跳过邮件发送")
        return
    try:
        import markdown2
        html = markdown2.markdown(markdown_text, extras=["tables", "fenced-code-blocks"])
    except Exception:
        html = "<pre>" + markdown_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"

    html = f"""<html><body style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;font-size:14px;line-height:1.7">
<h2 style="color:#1a56db;border-bottom:2px solid #1a56db;padding-bottom:8px">🎯 缠论三类买点扫描（chan.py）</h2>
{html}
<p style="color:#bbb;font-size:12px;margin-top:24px">— daily_stock_analysis · 中证500三买扫描 · 引擎 chan.py（经典纯 Python 缠论框架）</p>
</body></html>"""

    domain = sender.split("@")[-1].lower()
    smtp_map = {
        "qq.com": ("smtp.qq.com", 465, True), "foxmail.com": ("smtp.qq.com", 465, True),
        "163.com": ("smtp.163.com", 465, True), "126.com": ("smtp.126.com", 465, True),
        "gmail.com": ("smtp.gmail.com", 587, False),
        "outlook.com": ("smtp-mail.outlook.com", 587, False),
        "hotmail.com": ("smtp-mail.outlook.com", 587, False),
        "sina.com": ("smtp.sina.com", 465, True), "sohu.com": ("smtp.sohu.com", 465, True),
    }
    host, port, ssl = smtp_map.get(domain, (f"smtp.{domain}", 465, True))
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((os.getenv("EMAIL_SENDER_NAME", "股票分析助手"), sender))
    msg["To"] = receivers
    try:
        if ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(sender, password)
        recv_list = [r.strip() for r in receivers.split(",") if r.strip()]
        server.sendmail(sender, recv_list, msg.as_string())
        server.quit()
        print(f"✅ 邮件已发送: {receivers}")
    except Exception as e:
        print(f"⚠️ 邮件发送失败: {type(e).__name__}: {str(e)[:150]}（不影响报告文件）")


def main():
    universe = load_universe()
    if SCAN_LIMIT > 0:
        universe = universe[:SCAN_LIMIT]
    print(f"[开始] 股票池 {len(universe)} 只 | 数据 {SCAN_START} ~ {SCAN_END} | 判定：最新买卖点=三类买点(3a/3b)")

    hits, fails, short = [], [], []
    total = len(universe)
    for i, (code, name) in enumerate(universe, 1):
        try:
            df = get_bars_df(code, "stock", SCAN_START, SCAN_END)
            if len(df) < 120:
                short.append((code, name, len(df)))
                print(f"[{i}/{total}] {name}({code}) 数据不足 {len(df)} 根，跳过")
                continue
            kl_list, chan = run_chan(df, code, "stock", SCAN_START, SCAN_END)
            bsps = kl_list.bs_point_lst.getSortedBspList()
            latest = bsps[-1] if bsps else None
            if is_third_buy(latest):
                t = ",".join(x.value for x in latest.type)
                hits.append((name, code, t, ctime_str(latest.klu.time),
                             round(latest.klu.close, 3), round(df.iloc[-1]["close"], 3)))
                print(f"[{i}/{total}] ✅ 命中 {name}({code}) 三买{t} @{ctime_str(latest.klu.time)} {latest.klu.close:.2f}")
            elif i % 50 == 0 or i == total:
                print(f"[{i}/{total}] 已扫描 {i} 只，命中 {len(hits)} 只（{name}({code}) 最新买卖点非三买）")
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
        except Exception as e:
            fails.append((code, name, f"{type(e).__name__}: {str(e)[:120]}"))
            print(f"[{i}/{total}] ❌ {name}({code}) 失败：{type(e).__name__}: {str(e)[:100]}")
            continue

    # ---- 生成报告 ----
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 🎯 缠论三类买点扫描结果（chan.py）",
        f"> 扫描时间：{now} ｜ 股票池：{SCAN_UNIVERSE}（{total} 只）｜ 数据区间：{SCAN_START} ~ {SCAN_END}",
        f"> 判定口径：**最新一个买卖点 = 第三类买点（3a/3b）**\n",
    ]
    if hits:
        lines.append(f"## ✅ 命中 {len(hits)} 只（最新买卖点为三买）\n")
        lines.append("| 序号 | 名称 | 代码 | 三买类型 | 三买时间 | 三买价格 | 最新收盘 |")
        lines.append("|---|---|---|---|---|---|---|")
        for k, (name, code, t, tm, price, last) in enumerate(hits, 1):
            lines.append(f"| {k} | {name} | {code} | {t} | {tm} | {price} | {last} |")
        lines.append("")
    else:
        lines.append("## 😴 本次未命中\n> 中证500 中无最新买卖点为三类买点的股票（可改天再扫，或用 SCAN_UNIVERSE=custom:代码 测试）\n")

    lines.append("## 📊 统计")
    lines.append(f"- 扫描总数：{total}")
    lines.append(f"- 分析成功：{total - len(fails) - len(short)}")
    lines.append(f"- 数据不足(<120根)：{len(short)}" + (f"（{', '.join(n for _, n, _ in short[:10])}）" if short else ""))
    lines.append(f"- 失败：{len(fails)}" + (f"（{', '.join(f'{n}:{err[:30]}' for _, n, err in fails[:10])}）" if fails else ""))
    report = "\n".join(lines)

    with open("chan_scan_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n🎉 扫描完成：命中 {len(hits)} 只 | 报告 chan_scan_report.md")

    subject = f"缠论三买扫描：命中{len(hits)}只（{now}）"
    send_email(subject, report)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
