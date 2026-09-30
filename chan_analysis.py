# -*- coding: utf-8 -*-
"""
CZSC 批量缠论分析 + 绘图 + 三类买卖点识别 + 背驰判断
适配当前仓库、兼容 akshare、支持自定义起止时间
标的：11只个股 + 5大指数
"""
import os
import akshare as ak
import pandas as pd
from czsc import CZSC, RawBar
from czsc.utils import save_kline_image

# ====================== 【可自由修改参数】======================
START_DATE = "20240101"   # 数据起始时间
END_DATE = ""              # 结束时间，留空=最新

# 分析标的列表
stock_list = [
    ("京东方A", "000725"),
    ("格力电器", "000651"),
    ("长安汽车", "000625"),
    ("海南橡胶", "601118"),
    ("科大讯飞", "002230"),
    ("中国宝安", "000009"),
    ("海格通信", "002465"),
    ("郑州银行", "002936"),
    ("中国平安", "601318"),
    ("美的集团", "000333"),
    ("比亚迪", "002594"),
    ("上证指数", "000001"),
    ("深证指数", "399001"),
    ("创业板指", "399006"),
    ("沪深300", "000300"),
    ("上证50", "000016"),
]
# =================================================================

# 新建图片文件夹
img_dir = "chan_img"
os.makedirs(img_dir, exist_ok=True)

report_lines = []
report_lines.append("# 批量缠论分析报告（含买卖点+背驰判断）")
report_lines.append(f"> 数据区间：{START_DATE} ~ {END_DATE if END_DATE else '最新交易日'}\n")


def get_ak_bars(code, start, end):
    """获取akshare日线并转为czsc标准K线"""
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq"
    )
    bars = []
    for _, row in df.iterrows():
        bar = RawBar(
            dt=row["日期"],
            open=row["开盘"],
            close=row["收盘"],
            high=row["最高"],
            low=row["最低"],
            vol=row["成交量"]
        )
        bars.append(bar)
    return bars, df


def check_bi_bei_chi(c: CZSC):
    """
    趋势背驰 / 盘整背驰 判断
    返回：背驰结论、是否趋势背驰、是否盘整背驰
    """
    bi_list = c.bi_list
    if len(bi_list) < 5:
        return "K线笔数不足，无法判断背驰", False, False

    last_bi = bi_list[-1]
    prev_bi = bi_list[-2]

    # 简单力度比较：最后一笔与前一笔幅度对比
    last_power = abs(last_bi.high - last_bi.low)
    prev_power = abs(prev_bi.high - prev_bi.low)

    trend_bc = False
    pingzheng_bc = False
    res = "无背驰"

    # 下跌末端背驰
    if last_bi.direction == "down":
        if last_power < prev_power * 0.8:
            # 观察是否有连续同向笔构成趋势
            if len(bi_list) >= 7 and bi_list[-3].direction == "down":
                res = "✅ 下跌趋势背驰（大概率阶段性底部）"
                trend_bc = True
            else:
                res = "✅ 下跌盘整背驰（短线反弹机会）"
                pingzheng_bc = True

    # 上涨末端背驰
    if last_bi.direction == "up":
        if last_power < prev_power * 0.8:
            if len(bi_list) >= 7 and bi_list[-3].direction == "up":
                res = "❌ 上涨趋势背驰（大概率阶段顶部）"
                trend_bc = True
            else:
                res = "❌ 上涨盘整背驰（短线回调风险）"
                pingzheng_bc = True

    return res, trend_bc, pingzheng_bc


def get_buy_sell_points(c: CZSC):
    """
    识别一类、二类、三类买卖点
    """
    buy1 = buy2 = buy3 = 0
    sell1 = sell2 = sell3 = 0

    zs_list = c.zs_list
    bi_list = c.bi_list

    if len(bi_list) < 6 or len(zs_list) == 0:
        return "数据不足，未识别买卖点", buy1, buy2, buy3, sell1, sell2, sell3

    last_bi = bi_list[-1]
    last_zs = zs_list[-1]

    # 一类买卖点：趋势背驰末端
    bc_res, is_trend, _ = check_bi_bei_chi(c)
    if is_trend:
        if last_bi.direction == "down":
            buy1 = 1
        elif last_bi.direction == "up":
            sell1 = 1

    # 二类买卖点：不创新高/新低折返
    if len(bi_list) >= 8:
        pre_last = bi_list[-2]
        if last_bi.direction == "up" and pre_last.low > last_zs.low:
            buy2 = 1
        if last_bi.direction == "down" and pre_last.high < last_zs.high:
            sell2 = 1

    # 三类买卖点：突破中枢后回踩不进中枢
    if last_bi.direction == "up" and last_bi.low > last_zs.high:
        buy3 = 1
    if last_bi.direction == "down" and last_bi.high < last_zs.low:
        sell3 = 1

    msg_list = []
    if buy1: msg_list.append("一类买点")
    if buy2: msg_list.append("二类买点")
    if buy3: msg_list.append("三类买点")
    if sell1: msg_list.append("一类卖点")
    if sell2: msg_list.append("二类卖点")
    if sell3: msg_list.append("三类卖点")

    return "、".join(msg_list) if msg_list else "无明显三类买卖点", buy1, buy2, buy3, sell1, sell2, sell3


# ====================== 批量遍历所有标的 ======================
for stock_name, stock_code in stock_list:
    print(f"===== 处理中：{stock_name}({stock_code}) =====")
    try:
        bars, df = get_ak_bars(stock_code, START_DATE, END_DATE)
        if len(bars) < 120:
            report_lines.append(f"## {stock_name}({stock_code})\n> ⚠️ K线数量过少，跳过分析\n\n")
            continue

        c = CZSC(bars)

        # 背驰判断
        bc_result, _, _ = check_bi_bei_chi(c)
        # 买卖点识别
        point_result, b1, b2, b3, s1, s2, s3 = get_buy_sell_points(c)

        # 保存缠论K线图
        img_path = os.path.join(img_dir, f"{stock_code}_{stock_name}.png")
        save_kline_image(c, img_path)

        # 最新数据
        last_price = df.iloc[-1]["收盘"]
        bi_count = len(c.bi_list)
        zs_count = len(c.zs_list)

        # 最后一笔信息
        last_bi_info = "无"
        if c.bi_list:
            b = c.bi_list[-1]
            last_bi_info = f"{b.direction} | 区间：{b.dt_range[0]} ~ {b.dt_range[1]} | 高低点：{b.low} / {b.high}"

        # 写入报告
        report_lines.append(f"## {stock_name}（{stock_code}）")
        report_lines.append(f"- 最新收盘价：{last_price}")
        report_lines.append(f"- 有效K线数量：{len(bars)}")
        report_lines.append(f"- 笔总数：{bi_count}")
        report_lines.append(f"- 中枢总数：{zs_count}")
        report_lines.append(f"- 最新一笔走势：{last_bi_info}")
        report_lines.append(f"- 缠论背驰判断：**{bc_result}**")
        report_lines.append(f"- 三类买卖点识别：**{point_result}**")
        report_lines.append(f"![{stock_name}缠论图谱]({img_path})")
        report_lines.append("\n")

        print(f"✅ {stock_name} 分析完成")

    except Exception as e:
        err = f"❌ {stock_name} 分析失败：{str(e)}"
        print(err)
        report_lines.append(f"## {stock_name}({stock_code})\n{err}\n\n")


# 保存最终报告
with open("chan_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))

print("\n🎉 全部完成：已生成【三类买卖点+背驰判断+缠论绘图】完整报告")
