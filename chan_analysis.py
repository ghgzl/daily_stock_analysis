# -*- coding: utf-8 -*-
"""
CZSC批量缠论分析绘图脚本
标的：个股+主流指数，akshare数据源
可修改 START_DATE / END_DATE 调整时间区间
"""
import os
import akshare as ak
import pandas as pd
from czsc import CZSC, RawBar
from czsc.utils import save_kline_image

# ====================== 【可修改参数区】======================
START_DATE = "20240101"   # 起始时间
END_DATE = ""             # 结束时间，留空自动取最新交易日

# 标的列表：名称, akshare代码
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
# ============================================================

# 创建图片存放目录
img_dir = "chan_img"
if not os.path.exists(img_dir):
    os.makedirs(img_dir)

report_lines = []
report_lines.append("# 批量标的缠论分析报告\n")
report_lines.append(f"> 数据区间：{START_DATE} ~ {END_DATE if END_DATE else '最新'}\n\n")


def get_ak_bars(code, start, end):
    """从akshare获取日线，转换czsc RawBar"""
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


for stock_name, stock_code in stock_list:
    print(f"===== 正在处理：{stock_name}({stock_code}) =====")
    try:
        bars, df = get_ak_bars(stock_code, START_DATE, END_DATE)
        if len(bars) < 100:
            report_lines.append(f"## {stock_name}({stock_code})\n> ⚠️K线数量不足100根，跳过分析\n\n")
            continue

        # czsc 缠论计算
        c = CZSC(bars)

        # 绘制并保存图片
        img_path = os.path.join(img_dir, f"{stock_code}_{stock_name}.png")
        save_kline_image(c, img_path)

        # 整理分析信息
        last_bi = c.bi_list[-1] if len(c.bi_list) > 0 else None
        bi_dir = last_bi.direction if last_bi else "无"
        bi_start = last_bi.dt_range[0] if last_bi else "-"
        bi_end = last_bi.dt_range[1] if last_bi else "-"

        report_lines.append(f"## {stock_name}({stock_code})")
        report_lines.append(f"- 最新收盘价：{df.iloc[-1]['收盘']}")
        report_lines.append(f"- K线根数：{len(bars)}")
        report_lines.append(f"- 笔总数：{len(c.bi_list)}")
        report_lines.append(f"- 中枢总数：{len(c.zs_list)}")
        report_lines.append(f"- 最后一笔方向：{bi_dir}，时间区间：{bi_start} ~ {bi_end}")
        report_lines.append(f"![{stock_name}缠论图]({img_path})")
        report_lines.append("\n")
        print(f"✅ {stock_name} 完成")

    except Exception as e:
        err_msg = f"❌ {stock_name} 处理失败：{str(e)}"
        print(err_msg)
        report_lines.append(f"## {stock_name}({stock_code})\n{err_msg}\n\n")


# 写入报告
full_report = "\n".join(report_lines)
with open("chan_report.md", "w", encoding="utf-8") as f:
    f.write(full_report)

print("\n🎉全部任务完成，报告保存至 chan_report.md，图片保存在 chan_img/ 文件夹")
