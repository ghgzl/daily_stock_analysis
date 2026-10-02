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

# 本机支持 scan.env 配置邮件与扫描参数（云端用系统环境变量）
# override=True：让 scan.env 覆盖 start_scan.bat 里的默认值，用户改参数只需编辑 scan.env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan.env"), override=True)
except Exception:
    pass

# ====================== 【可修改参数】======================
SCAN_UNIVERSE = os.getenv("SCAN_UNIVERSE", "cs500")   # all_a(全A股) / cs500 / hs300 / custom:代码,代码
SCAN_START = os.getenv("SCAN_START", "2004-01-01")    # 数据起始
SCAN_END = os.getenv("SCAN_END", "") or datetime.now().strftime("%Y-%m-%d")
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "0"))        # 0=全部
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "8"))    # 并发线程数（全A建议8-16，越大越快但易被限流）
SCAN_RESUME = os.getenv("SCAN_RESUME", "1") == "1"    # 断点续跑：1=跳过上次已扫描的股票（默认开）
SLEEP_MIN, SLEEP_MAX = 0.1, 0.4                       # 每只股票随机小延迟，防限流
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(BASE_DIR, "scan_progress.csv")  # 断点记录文件
# =========================================================


def load_universe():
    """返回 [(代码, 名称), ...]
    all_a=沪深全A股 ｜ cs500=中证500 ｜ hs300=沪深300 ｜ custom:代码,代码
    兜底：akshare 全部失败时读仓库内置清单（all_a.csv / cs500.csv / hs300.csv），保证断网/海外也可跑"""
    import akshare as ak
    if SCAN_UNIVERSE.startswith("custom:"):
        codes = [c.strip() for c in SCAN_UNIVERSE.split(":", 1)[1].split(",") if c.strip()]
        return [(c, c) for c in codes]

    label = "全部A股" if SCAN_UNIVERSE == "all_a" else ("中证500" if SCAN_UNIVERSE == "cs500" else "沪深300")
    csv_path = os.path.join(BASE_DIR, f"{SCAN_UNIVERSE}.csv")
    symbol = "000905" if SCAN_UNIVERSE == "cs500" else ("000300" if SCAN_UNIVERSE == "hs300" else None)

    # ---- all_a：全A股清单（东财 沪深A股 代码+名称）----
    if SCAN_UNIVERSE == "all_a":
        for fn, kwargs in (("stock_info_a_code_name", {}), ("stock_zh_a_spot_em", {})):
            try:
                df = getattr(ak, fn)(**kwargs)
                if df is None or len(df) == 0:
                    continue
                code_col = next((c for c in ("code", "代码") if c in df.columns), None)
                name_col = next((c for c in ("name", "名称") if c in df.columns), None)
                if code_col is None:
                    print(f"[股票池] 接口 {fn} 列名无法识别（{list(df.columns)[:6]}），尝试下一个…")
                    continue
                name_col = name_col or code_col
                codes = [str(x).zfill(6) for x in df[code_col].tolist()]
                names = [str(x) for x in df[name_col].tolist()]
                items = [(c, n) for c, n in zip(codes, names) if c[:1] in "0369"]  # 沪深A股（000/001/002/003/300/301/600/601/603/605/688/689）
                print(f"[股票池] 全A股 {len(items)} 只（akshare 接口 {fn}）")
                return items
            except Exception as e:
                print(f"[股票池] 接口 {fn} 失败：{type(e).__name__}: {str(e)[:100]}，尝试下一个…")

    # ---- cs500 / hs300：中证官网 → 东财 → 旧接口 ----
    if symbol:
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

    # ---- 兜底：仓库内置清单 ----
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
    raise RuntimeError(f"无法获取{label}清单（akshare 接口全部失败且无内置清单）")


def load_done_codes():
    """读取断点记录：返回已扫描完成的代码集合"""
    done = set()
    if SCAN_RESUME and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            for line in f:
                code = line.strip().split(",")[0]
                if code:
                    done.add(code)
    return done


def scan_one(item):
    """单只股票：拉数据 → chan.py 分析 → 判定最新买卖点是否三买。返回 (状态, ...)"""
    code, name = item
    try:
        df = get_bars_df(code, "stock", SCAN_START, SCAN_END)
        if len(df) < 120:
            return ("short", code, name, len(df))
        kl_list, chan = run_chan(df, code, "stock", SCAN_START, SCAN_END)
        bsps = kl_list.bs_point_lst.getSortedBspList()
        latest = bsps[-1] if bsps else None
        if is_third_buy(latest):
            t = ",".join(x.value for x in latest.type)
            return ("hit", name, code, t, ctime_str(latest.klu.time),
                    round(latest.klu.close, 3), round(df.iloc[-1]["close"], 3))
        return ("ok", code, name, None)
    except Exception as e:
        return ("fail", code, name, f"{type(e).__name__}: {str(e)[:120]}")


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
<p style="color:#bbb;font-size:12px;margin-top:24px">— daily_stock_analysis · 三类买点扫描 · 引擎 chan.py（经典纯 Python 缠论框架）</p>
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
    from concurrent.futures import ThreadPoolExecutor, as_completed

    universe = load_universe()
    if SCAN_LIMIT > 0:
        universe = universe[:SCAN_LIMIT]
    total = len(universe)
    print(f"[开始] 股票池 {total} 只 | 数据 {SCAN_START} ~ {SCAN_END} | 判定：最新买卖点=三类买点(3a/3b)")

    # 断点续跑：跳过上次已完成代码
    done_codes = load_done_codes()
    todo = [(c, n) for c, n in universe if c not in done_codes]
    skipped = total - len(todo)
    if skipped:
        print(f"[断点] 跳过已扫描 {skipped} 只（上次完成，可删 scan_progress.csv 强制全量重扫）")

    hits, fails, short = [], [], []
    completed = 0
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = {ex.submit(scan_one, item): item for item in todo}
        for fut in as_completed(futures):
            r = fut.result()
            code = r[2] if r[0] == "hit" else r[1]  # hit 元组第3项才是代码
            # 记录断点（线程安全：as_completed 消费线程唯一）
            with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{code},{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            if r[0] == "hit":
                name, c, t, tm, price, last = r[1], r[2], r[3], r[4], r[5], r[6]
                hits.append((name, c, t, tm, price, last))
                print(f"✅ 命中 {name}({c}) 三买{t} @{tm} {price}")
            elif r[0] == "short":
                c, n, nbar = r[1], r[2], r[3]
                short.append((c, n, nbar))
            elif r[0] == "fail":
                c, n, err = r[1], r[2], r[3]
                fails.append((c, n, err))
                print(f"❌ {n}({c}) 失败：{err[:80]}")
            completed += 1
            if completed % 50 == 0 or completed == len(todo):
                print(f"[进度] {completed}/{len(todo)}（跳过{skipped}）| 命中 {len(hits)} 只")

    # ---- 生成报告 ----
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    universe_label = "全部A股" if SCAN_UNIVERSE == "all_a" else SCAN_UNIVERSE
    lines = [
        f"# 🎯 缠论三类买点扫描结果（chan.py）",
        f"> 扫描时间：{now} ｜ 股票池：{universe_label}（{total} 只）｜ 数据区间：{SCAN_START} ~ {SCAN_END}",
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
        lines.append("## 😴 本次未命中\n> 当前股票池中无最新买卖点为三类买点的股票\n")

    lines.append("## 📊 统计")
    lines.append(f"- 扫描总数：{total}")
    lines.append(f"- 本次实际分析：{completed}（断点跳过 {skipped}）")
    lines.append(f"- 分析成功：{completed - len(fails) - len(short)}")
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
