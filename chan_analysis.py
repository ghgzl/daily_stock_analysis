# -*- coding: utf-8 -*-
"""
CZSC 批量缠论分析 + 绘图 + 三类买卖点识别 + 背驰判断 (适配 czsc 0.9.51)
标的：11只个股 + 5大指数；akshare 数据源；可自定义起止时间
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import akshare as ak
from czsc import CZSC, RawBar, Freq
from czsc.utils.sig import get_zs_seq

# ====================== 【可自由修改参数】======================
START_DATE = "20240101"   # 数据起始时间
END_DATE = ""              # 结束时间，留空=最新

# 分析标的：名称, 代码, 类型(stock=个股 / index=指数)
stock_list = [
    ("京东方A",  "000725", "stock"),
    ("格力电器", "000651", "stock"),
    ("长安汽车", "000625", "stock"),
    ("海南橡胶", "601118", "stock"),
    ("科大讯飞", "002230", "stock"),
    ("中国宝安", "000009", "stock"),
    ("海格通信", "002465", "stock"),
    ("郑州银行", "002936", "stock"),
    ("中国平安", "601318", "stock"),
    ("美的集团", "000333", "stock"),
    ("比亚迪",   "002594", "stock"),
    ("上证指数", "000001", "index"),
    ("深证指数", "399001", "index"),
    ("创业板指", "399006", "index"),
    ("沪深300",  "000300", "index"),
    ("上证50",   "000016", "index"),
]
# =================================================================

# 中文字体设置
plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

img_dir = "chan_img"
os.makedirs(img_dir, exist_ok=True)

report_lines = ["# 批量缠论分析报告（含买卖点+背驰判断）",
                f"> 数据区间：{START_DATE} ~ {END_DATE if END_DATE else '最新交易日'}\n"]


def get_bars(code, kind, start, end):
    """获取K线：yfinance(Yahoo,海外可用) -> akshare东财 -> akshare新浪
    个股后缀：6xx->.SS，0xx/3xx->.SZ；指数用映射表
    """
    import time
    import pandas as pd
    last_err = None

    def norm_rename(df):
        return df.rename(columns={
            "日期": "dt", "date": "dt", "Date": "dt",
            "开盘": "open", "open": "open", "Open": "open",
            "收盘": "close", "close": "close", "Close": "close",
            "最高": "high", "high": "high", "High": "high",
            "最低": "low", "low": "low", "Low": "low",
            "成交量": "vol", "volume": "vol", "Volume": "vol",
            "成交额": "amount", "amount": "amount",
        })

    def to_bars(df):
        df = norm_rename(df)
        need = ["dt", "open", "close", "high", "low", "vol"]
        if not all(c in df.columns for c in need):
            raise ValueError(f"列不完整: {list(df.columns)}")
        bars = []
        for i, (_, row) in enumerate(df.iterrows()):
            bars.append(RawBar(
                symbol=code, id=i, dt=pd.Timestamp(row["dt"]), freq=Freq.D,
                open=float(row["open"]), close=float(row["close"]),
                high=float(row["high"]), low=float(row["low"]),
                vol=float(row["vol"]), amount=float(row.get("amount", 0) or 0),
            ))
        if len(bars) < 10:
            raise ValueError(f"K线不足({len(bars)})")
        return bars, df

    # 1) yfinance (Yahoo) —— GitHub Actions 海外服务器首选
    try:
        import yfinance as yf
        yf_code = None
        if kind == "index":
            idx_map = {"000001": "000001.SS", "399001": "399001.SZ",
                       "399006": "399006.SZ", "000300": "000300.SS", "000016": "000016.SS"}
            yf_code = idx_map.get(code)
        else:
            yf_code = code + (".SS" if code.startswith("6") else ".SZ")
        if yf_code:
            start_s = start[:4] + "-" + start[4:6] + "-" + start[6:]
            end_s = end[:4] + "-" + end[4:6] + "-" + end[6:]
            df = yf.download(yf_code, start=start_s, end=end_s,
                             auto_adjust=False, progress=False, threads=False, timeout=20)
            if df is not None and not df.empty:
                df = df.reset_index()
                if hasattr(df.columns, "get_level_values"):
                    df.columns = df.columns.get_level_values(0)
                return to_bars(df)
    except Exception as e:
        last_err = f"yfinance: {type(e).__name__}: {str(e)[:80]}"

    # 2) akshare 东财 -> 新浪（本地/国内可用）
    sources = []
    if kind == "index":
        sources = [
            ("index_zh_a_hist", lambda: ak.index_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end)),
            ("stock_zh_index_daily", lambda: ak.stock_zh_index_daily(
                symbol=("sh" if code.startswith("000") or code.startswith("60") else "sz") + code)),
        ]
    else:
        sources = [
            ("stock_zh_a_hist", lambda: ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")),
            ("stock_zh_a_daily", lambda: ak.stock_zh_a_daily(
                symbol=("sh" if code.startswith("6") else "sz") + code,
                start_date=start[:4] + "-" + start[4:6] + "-" + start[6:], adjust="qfq")),
        ]

    for name, fetcher in sources:
        try:
            df = fetcher()
            if df is None or df.empty:
                last_err = f"{name} 返回空数据"
                continue
            return to_bars(df)
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {str(e)[:80]}"
            time.sleep(2)

    raise ConnectionError(f"所有数据源失败: {last_err}")


def check_bei_chi(c):
    """趋势/盘整背驰判断：比较最后两笔的波动幅度"""
    bis = c.bi_list
    if len(bis) < 5:
        return "笔数不足，无法判断背驰", False, False
    last_power = abs(bis[-1].high - bis[-1].low)
    prev_power = abs(bis[-2].high - bis[-2].low)
    if prev_power == 0 or last_power >= prev_power * 0.8:
        return "无背驰", False, False
    if bis[-1].direction.value == "向下":
        trend = len(bis) >= 7 and bis[-3].direction.value == "向下"
        return ("下跌趋势背驰（关注阶段底部）" if trend
                else "下跌盘整背驰（短线反弹机会）"), trend, not trend
    if bis[-1].direction.value == "向上":
        trend = len(bis) >= 7 and bis[-3].direction.value == "向上"
        return ("上涨趋势背驰（警惕阶段顶部）" if trend
                else "上涨盘整背驰（短线回调风险）"), trend, not trend
    return "无背驰", False, False


def get_points(c, zs_list):
    """识别一/二/三类买卖点"""
    bis = c.bi_list
    if len(bis) < 6 or not zs_list:
        return "数据不足，未识别买卖点"
    bc_msg, is_trend, _ = check_bei_chi(c)
    last = bis[-1]
    zs = zs_list[-1]
    msgs = []
    if is_trend and last.direction.value == "向下":
        msgs.append("一类买点")
    if is_trend and last.direction.value == "向上":
        msgs.append("一类卖点")
    if len(bis) >= 8:
        pre = bis[-2]
        if last.direction.value == "向上" and pre.low > zs.zd:
            msgs.append("二类买点")
        if last.direction.value == "向下" and pre.high < zs.zg:
            msgs.append("二类卖点")
    if last.direction.value == "向上" and last.low > zs.zg:
        msgs.append("三类买点")
    if last.direction.value == "向下" and last.high < zs.zd:
        msgs.append("三类卖点")
    return "、".join(msgs) if msgs else "无明显三类买卖点"


def draw_chart(bars, c, zs_list, name, code, path):
    """matplotlib 绘制 K线 + 分型 + 笔 + 中枢，保存PNG"""
    dts = [b.dt for b in bars]
    idx = list(range(len(bars)))
    closes = [b.close for b in bars]
    opens = [b.open for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    fig, ax = plt.subplots(figsize=(16, 8))
    # K线蜡烛图
    for i in range(len(bars)):
        color = "#ef232a" if closes[i] >= opens[i] else "#14b143"
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1)
        ax.add_patch(Rectangle((i - 0.3, min(opens[i], closes[i])), 0.6,
                               abs(closes[i] - opens[i]) or 0.01, color=color))
    # 分型标记
    for fx in c.fx_list:
        try:
            xi = dts.index(fx.dt)
        except ValueError:
            continue
        mk = "^" if fx.mark.value == "底分型" else "v"
        color = "#14b143" if fx.mark.value == "底分型" else "#ef232a"
        ax.plot(xi, fx.fx, marker=mk, color=color, markersize=8)
    # 笔连线
    for bi in c.bi_list:
        try:
            xa, xb = dts.index(bi.fx_a.dt), dts.index(bi.fx_b.dt)
        except ValueError:
            continue
        ax.plot([xa, xb], [bi.fx_a.fx, bi.fx_b.fx], color="#0e6efd", linewidth=2)
    # 中枢矩形
    for zs in zs_list:
        try:
            x0, x1 = dts.index(zs.sdt), dts.index(zs.edt)
            ax.add_patch(Rectangle((x0, zs.zd), max(x1 - x0, 1), zs.zg - zs.zd,
                                   fill=True, alpha=0.18, color="#f9a825"))
        except ValueError:
            continue
    # 精简X轴刻度
    step = max(len(bars) // 8, 1)
    ax.set_xticks(idx[::step])
    ax.set_xticklabels([d.strftime("%Y-%m-%d") for d in dts[::step]], rotation=30, fontsize=9)
    ax.set_title(f"{name}({code}) 缠论分析 笔:{len(c.bi_list)} 中枢:{len(zs_list)}", fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


for name, code, kind in stock_list:
    print(f"===== 处理中：{name}({code}) [{kind}] =====")
    try:
        bars, df = get_bars(code, kind, START_DATE, END_DATE)
        if len(bars) < 120:
            report_lines.append(f"## {name}({code})\n> ⚠️ K线数量过少({len(bars)})，跳过\n")
            continue
        c = CZSC(bars)
        zs_list = get_zs_seq(c.bi_list)
        bc_msg, _, _ = check_bei_chi(c)
        pt_msg = get_points(c, zs_list)
        img_path = os.path.join(img_dir, f"{code}_{name}.png")
        draw_chart(bars, c, zs_list, name, code, img_path)

        last_bi = c.bi_list[-1] if c.bi_list else None
        bi_info = "无"
        if last_bi:
            bi_info = (f"{last_bi.direction.value} | "
                       f"{last_bi.sdt:%Y-%m-%d}~{last_bi.edt:%Y-%m-%d} | "
                       f"低{last_bi.low:.2f}/高{last_bi.high:.2f}")

        report_lines.append(f"## {name}（{code}）")
        report_lines.append(f"- 最新收盘价：{df.iloc[-1]['close']}")
        report_lines.append(f"- 有效K线数量：{len(bars)}")
        report_lines.append(f"- 笔总数：{len(c.bi_list)} | 中枢总数：{len(zs_list)}")
        report_lines.append(f"- 最新一笔走势：{bi_info}")
        report_lines.append(f"- 缠论背驰判断：**{bc_msg}**")
        report_lines.append(f"- 三类买卖点识别：**{pt_msg}**")
        report_lines.append(f"![{name}缠论图谱]({img_path})\n")
        print(f"✅ {name} 完成")
    except Exception as e:
        err = f"❌ {name} 分析失败：{type(e).__name__}: {str(e)[:200]}"
        print(err)
        report_lines.append(f"## {name}({code})\n{err}\n")

with open("chan_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))
print("\n🎉 全部完成：报告 chan_report.md + 图片 chan_img/ 已生成")


# ================= 发送缠论报告到邮箱 =================
def send_report_email():
    """把 chan_report.md 内容发到配置的邮箱（复用原系统 EMAIL_* 环境变量）"""
    import os, smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr

    sender = os.getenv("EMAIL_SENDER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    receivers = os.getenv("EMAIL_RECEIVERS", "")
    if not (sender and password and receivers):
        print("⚠️ 未配置 EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVERS，跳过邮件发送")
        return

    with open("chan_report.md", "r", encoding="utf-8") as f:
        md = f.read()

    # 图片相对路径转 GitHub 绝对链接
    md = md.replace("](chan_img/", "](https://raw.githubusercontent.com/ghgzl/daily_stock_analysis/main/chan_img/")
    # Markdown 转 HTML（表格、粗体、标题）
    try:
        import markdown2
        html = markdown2.markdown(md, extras=["tables", "fenced-code-blocks"])
    except Exception:
        html = "<pre>" + md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"

    html = f"""<html><body style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;font-size:14px;line-height:1.7">
<h2 style="color:#1a56db;border-bottom:2px solid #1a56db;padding-bottom:8px">📈 批量缠论分析报告</h2>
<p style="color:#888">数据区间：20240101 ~ 最新交易日 ｜ 个股+指数共16个标的</p>
{html}
<p style="color:#bbb;font-size:12px;margin-top:24px">— 由 daily_stock_analysis 自动生成 · 缠论分析（czsc）</p>
</body></html>"""

    # 按发件人域名识别 SMTP 服务器（与原系统一致）
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
    msg["Subject"] = Header("缠论分析报告（16标的·买卖点+背驰判断）", "utf-8")
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
        print(f"✅ 缠论报告已发送到邮箱: {receivers}")
    except Exception as e:
        print(f"⚠️ 邮件发送失败: {type(e).__name__}: {str(e)[:120]}（不影响报告文件）")


send_report_email()
