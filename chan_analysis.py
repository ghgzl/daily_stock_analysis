# -*- coding: utf-8 -*-
"""
chan.py 批量缠论分析 + 绘图 + 三类买卖点识别 + 背驰判断 (经典纯 Python 缠论框架)
标的：11只个股 + 5大指数；akshare -> yfinance 多源数据；可自定义起止时间
依赖：本仓库 chanlib/（vendored chan.py 源码）+ chan_core.py
"""
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 保证 chan_core / chanlib 可导入
from chan_core import get_bars_df, run_chan, check_bei_chi, format_bsp_type, ctime_str
from Common.CEnum import BI_DIR

# ====================== 【可自由修改参数】======================
START_DATE = "2024-01-01"   # 数据起始时间
END_DATE = ""                # 结束时间，留空=最新交易日

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

if not END_DATE:
    END_DATE = datetime.now().strftime("%Y-%m-%d")

# 中文字体设置
plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

img_dir = "chan_img"
os.makedirs(img_dir, exist_ok=True)

report_lines = ["# 批量缠论分析报告（chan.py 引擎 · 含买卖点+背驰判断）",
                f"> 数据区间：{START_DATE} ~ {END_DATE}\n"]


def draw_chart(df, kl_list, name, code, path):
    """matplotlib 绘制 K线 + 分型 + 笔 + 中枢，保存PNG（字段适配 chan.py）"""
    dts = [d.strftime("%Y-%m-%d") for d in df["dt"]]
    n = len(df)
    opens = df["open"].tolist()
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    def x_of(t_str):
        try:
            return dts.index(t_str)
        except ValueError:
            return None

    fig, ax = plt.subplots(figsize=(16, 8))
    # K线蜡烛图
    for i in range(n):
        color = "#ef232a" if closes[i] >= opens[i] else "#14b143"
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1)
        ax.add_patch(Rectangle((i - 0.3, min(opens[i], closes[i])), 0.6,
                               abs(closes[i] - opens[i]) or 0.01, color=color))
    # 笔 + 分型（笔端点即顶/底分型）
    for bi in kl_list.bi_list:
        try:
            b_klu, e_klu = bi.get_begin_klu(), bi.get_end_klu()
        except Exception:
            continue
        xa, xb = x_of(ctime_str(b_klu.time)), x_of(ctime_str(e_klu.time))
        if xa is None or xb is None:
            continue
        va, vb = bi.get_begin_val(), bi.get_end_val()
        ax.plot([xa, xb], [va, vb], color="#0e6efd", linewidth=2)
        mk = "v" if bi.dir == BI_DIR.UP else "^"
        color = "#ef232a" if bi.dir == BI_DIR.UP else "#14b143"
        ax.plot(xb, vb, marker=mk, color=color, markersize=8)
    # 中枢矩形
    for zs in kl_list.zs_list:
        try:
            x0, x1 = x_of(ctime_str(zs.begin.time)), x_of(ctime_str(zs.end.time))
        except Exception:
            continue
        if x0 is None or x1 is None:
            continue
        ax.add_patch(Rectangle((x0, zs.low), max(x1 - x0, 1), zs.high - zs.low,
                               fill=True, alpha=0.18, color="#f9a825"))
    # 精简X轴刻度
    step = max(n // 8, 1)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([dts[i] for i in range(0, n, step)], rotation=30, fontsize=9)
    ax.set_title(f"{name}({code}) 缠论分析 笔:{len(kl_list.bi_list)} 中枢:{len(kl_list.zs_list)}", fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def fmt_points(bsps):
    """chan.py 原生三类买卖点 -> 中文列表"""
    if not bsps:
        return "无明显三类买卖点"
    parts = []
    for bsp in bsps:
        kind = "买" if bsp.is_buy else "卖"
        t = ",".join(x.value for x in bsp.type)
        parts.append(f"{kind}{t}类@{ctime_str(bsp.klu.time)}({bsp.klu.close:.2f})")
    return "、".join(parts)


for name, code, kind in stock_list:
    print(f"===== 处理中：{name}({code}) [{kind}] =====")
    try:
        df = get_bars_df(code, kind, START_DATE, END_DATE)
        if len(df) < 120:
            report_lines.append(f"## {name}({code})\n> ⚠️ K线数量过少({len(df)})，跳过\n")
            continue
        kl_list, chan = run_chan(df, code, kind, START_DATE, END_DATE)
        bis = kl_list.bi_list
        zss = kl_list.zs_list
        bsps = kl_list.bs_point_lst.getSortedBspList()
        bc_msg, _, _ = check_bei_chi(kl_list)
        pt_msg = fmt_points(bsps)
        img_path = os.path.join(img_dir, f"{code}_{name}.png")
        draw_chart(df, kl_list, name, code, img_path)

        last_bi = bis[-1] if bis else None
        bi_info = "无"
        if last_bi:
            b_klu = last_bi.get_begin_klu()
            e_klu = last_bi.get_end_klu()
            d = "上涨" if last_bi.dir == BI_DIR.UP else "下跌"
            bi_info = (f"{d} | {ctime_str(b_klu.time)}~{ctime_str(e_klu.time)} | "
                       f"{last_bi.get_begin_val():.2f} → {last_bi.get_end_val():.2f}")

        report_lines.append(f"## {name}（{code}）")
        report_lines.append(f"- 最新收盘价：{df.iloc[-1]['close']}")
        report_lines.append(f"- 有效K线数量：{len(df)}")
        report_lines.append(f"- 笔总数：{len(bis)} | 中枢总数：{len(zss)}")
        report_lines.append(f"- 最新一笔走势：{bi_info}")
        report_lines.append(f"- 缠论背驰判断：**{bc_msg}**")
        report_lines.append(f"- 三类买卖点识别：**{pt_msg}**")
        report_lines.append(f"![{name}缠论图谱]({img_path})\n")
        print(f"✅ {name} 完成（笔{len(bis)} 中枢{len(zss)} 买卖点{len(bsps)}）")
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
<h2 style="color:#1a56db;border-bottom:2px solid #1a56db;padding-bottom:8px">📈 批量缠论分析报告（chan.py）</h2>
<p style="color:#888">数据区间：{START_DATE} ~ {END_DATE} ｜ 个股+指数共{len(stock_list)}个标的</p>
{html}
<p style="color:#bbb;font-size:12px;margin-top:24px">— 由 daily_stock_analysis 自动生成 · 缠论分析（chan.py 经典纯 Python 框架）</p>
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
    msg["Subject"] = Header(f"缠论分析报告（chan.py · {len(stock_list)}标的·买卖点+背驰判断）", "utf-8")
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
