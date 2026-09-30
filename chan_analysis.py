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
    """个股用 stock_zh_a_hist，指数用 index_zh_a_hist，统一转成 czsc K线"""
    if kind == "index":
        df = ak.index_zh_a_hist(symbol=code, period="daily",
                                start_date=start, end_date=end)
    else:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty:
        return [], df
    bars = []
    for i, (_, row) in enumerate(df.iterrows()):
        bars.append(RawBar(
            symbol=code, id=i, dt=row["日期"], freq=Freq.D,
            open=float(row["开盘"]), close=float(row["收盘"]),
            high=float(row["最高"]), low=float(row["最低"]),
            vol=float(row["成交量"]), amount=float(row.get("成交额", 0) or 0),
        ))
    return bars, df


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
        report_lines.append(f"- 最新收盘价：{df.iloc[-1]['收盘']}")
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
