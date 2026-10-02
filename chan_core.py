# -*- coding: utf-8 -*-
"""
缠论分析核心逻辑（chan.py 引擎）—— 与 UI 解耦，可独立测试
功能：数据获取 / 名称解析 / CChan 分析 / 背驰判断 / ECharts option 生成
"""
import os
import sys
import time

import pandas as pd

# vendored chan.py 源码路径（本仓库 chanlib/ 目录）
CHANLIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chanlib")
if CHANLIB not in sys.path:
    sys.path.insert(0, CHANLIB)

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import KL_TYPE, BI_DIR
from DataAPI.YfDataSrc import CYfDataSrc

DEFAULT_START = "2024-01-01"
MIN_K_LINES = 600  # 用户要求的提醒阈值


# ==================== 数据获取 ====================
def get_bars_df(code, kind, start, end):
    """获取K线 DataFrame（dt/open/close/high/low/vol/amount）
    数据源顺序由环境变量 CHAN_DATA_SOURCE 控制：
      auto    = yfinance(Yahoo) -> akshare东财 -> akshare新浪  （默认，GitHub Actions 海外用）
      akshare = akshare东财 -> akshare新浪 -> yfinance          （国内本机推荐，避免 yahoo 超时）
      yfinance= 仅 yfinance
    个股后缀：6xx->.SS，0xx/3xx->.SZ；指数用映射表
    """
    import akshare as ak
    source_mode = os.getenv("CHAN_DATA_SOURCE", "auto").lower()
    errs = []

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

    def to_df(raw):
        df = norm_rename(raw)
        need = ["dt", "open", "close", "high", "low", "vol"]
        if not all(c in df.columns for c in need):
            raise ValueError(f"列不完整: {list(df.columns)}")
        df = df.dropna(subset=need).reset_index(drop=True)
        df["dt"] = pd.to_datetime(df["dt"])
        for c in ["open", "close", "high", "low", "vol"]:
            df[c] = df[c].astype(float)
        # chan.py 引擎数据校验严格（要求 high>=open/close 且 low<=open/close）
        # Yahoo/第三方数据偶有 high<close 的情况，此处强制修正，避免 CChanException
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)
        if "amount" not in df.columns:
            df["amount"] = 0.0
        if len(df) < 10:
            raise ValueError(f"K线不足({len(df)})")
        return df

    def fetch_yfinance():
        """成功返回 df，失败返回 None"""
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
                raw = yf.download(yf_code, start=start, end=end,
                                  auto_adjust=False, progress=False, threads=False, timeout=12)
                if raw is not None and not raw.empty:
                    raw = raw.reset_index()
                    if hasattr(raw.columns, "get_level_values"):
                        raw.columns = raw.columns.get_level_values(0)
                    return to_df(raw)
        except Exception as e:
            errs.append(f"yfinance: {type(e).__name__}: {str(e)[:80]}")
        return None

    def fetch_akshare():
        """akshare 东财 -> 新浪；成功返回 df，失败返回 None"""
        start_n = start.replace("-", "")
        end_n = end.replace("-", "")
        sources = []
        if kind == "index":
            sources = [
                ("index_zh_a_hist", lambda: ak.index_zh_a_hist(
                    symbol=code, period="daily", start_date=start_n, end_date=end_n)),
                ("stock_zh_index_daily", lambda: ak.stock_zh_index_daily(
                    symbol=("sh" if code.startswith("000") or code.startswith("60") else "sz") + code)),
            ]
        else:
            sources = [
                ("stock_zh_a_hist", lambda: ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start_n, end_date=end_n, adjust="qfq")),
                ("stock_zh_a_daily", lambda: ak.stock_zh_a_daily(
                    symbol=("sh" if code.startswith("6") else "sz") + code,
                    start_date=start, adjust="qfq")),
            ]
        for name, fetcher in sources:
            try:
                raw = fetcher()
                if raw is None or raw.empty:
                    errs.append(f"{name} 返回空数据")
                    continue
                return to_df(raw)
            except Exception as e:
                errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
                time.sleep(2)
        return None

    if source_mode == "akshare":
        df = fetch_akshare()
        if df is None:
            df = fetch_yfinance()
    elif source_mode == "yfinance":
        df = fetch_yfinance()
    else:  # auto：yfinance 优先（海外 Actions 环境）
        df = fetch_yfinance()
        if df is None:
            df = fetch_akshare()
    if df is None:
        raise ConnectionError(f"所有数据源失败: {' | '.join(errs[-3:])}")
    return df


# ==================== 缠论判断（复刻原 chan_analysis.py 口径，基于 chan.py 笔结构） ====================
def check_bei_chi(kl_list):
    """趋势/盘整背驰判断：比较最后两笔的波动幅度（与原 czsc 版口径一致）"""
    bis = kl_list.bi_list
    if len(bis) < 5:
        return "笔数不足，无法判断背驰", False, False
    last_power = bis[-1].amp()
    prev_power = bis[-2].amp()
    if prev_power == 0 or last_power >= prev_power * 0.8:
        return "无背驰", False, False
    if bis[-1].dir == BI_DIR.DOWN:
        trend = len(bis) >= 7 and bis[-3].dir == BI_DIR.DOWN
        return ("下跌趋势背驰（关注阶段底部）" if trend
                else "下跌盘整背驰（短线反弹机会）"), trend, not trend
    if bis[-1].dir == BI_DIR.UP:
        trend = len(bis) >= 7 and bis[-3].dir == BI_DIR.UP
        return ("上涨趋势背驰（警惕阶段顶部）" if trend
                else "上涨盘整背驰（短线回调风险）"), trend, not trend
    return "无背驰", False, False


def format_bsp_type(bsp):
    """买卖点类型 -> 中文显示，如 1->一类买点/一类卖点"""
    main = bsp.type[0].main_type()  # '1'/'2'/'3'
    sub = ",".join(x.value for x in bsp.type)
    kind = "买" if bsp.is_buy else "卖"
    return f"{kind}{main}类({sub})"


# ==================== 名称/代码解析 ====================
INDEX_MAP = {
    "000001": "上证指数", "399001": "深证指数", "399006": "创业板指",
    "000300": "沪深300", "000016": "上证50",
}
# 指数名称反向映射（输入名称也能识别指数）
INDEX_NAME_MAP = {v: (k, v, "index") for k, v in INDEX_MAP.items()}

# 常用股票内置兜底表（akshare 拉取失败时仍可解析常用标的）
FALLBACK_STOCKS = {
    "000725": "京东方A", "000651": "格力电器", "000625": "长安汽车",
    "601118": "海南橡胶", "002230": "科大讯飞", "000009": "中国宝安",
    "002465": "海格通信", "002936": "郑州银行", "601318": "中国平安",
    "000333": "美的集团", "002594": "比亚迪", "600519": "贵州茅台",
    "601398": "工商银行", "600036": "招商银行",
    "601988": "中国银行", "601857": "中国石油",
    "600900": "长江电力", "601899": "紫金矿业", "600030": "中信证券",
}


def get_stock_map():
    """全量A股代码→名称映射（akshare）；失败返回内置兜底表"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()  # columns: code, name
        if df is not None and len(df) > 1000:
            return df
    except Exception:
        pass
    # 兜底：内置常用股票表
    df = pd.DataFrame(
        [{"code": c, "name": n} for c, n in FALLBACK_STOCKS.items()])
    return df


def _norm(s):
    """全角→半角归一化（akshare 名称里 '京东方Ａ' 是全角 Ａ）"""
    import unicodedata
    out = []
    for ch in str(s):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).upper().replace(" ", "")


def resolve_symbol(user_input, stock_map=None):
    """输入名称/代码 -> (code, name, kind)；找不到返回 None
    stock_map 可注入（便于测试），默认内部获取
    """
    text = _norm(user_input)
    # 1) 指数名称直接命中（如"上证指数"）
    if text in INDEX_NAME_MAP:
        return INDEX_NAME_MAP[text]
    # 2) 指数代码命中（000001=上证指数；浦发银行请输入"浦发银行"或 600000）
    if text in INDEX_MAP:
        return text, INDEX_MAP[text], "index"
    df = stock_map if stock_map is not None else get_stock_map()
    # 3) 纯数字代码 → 先查股票表
    if text.isdigit() and len(text) == 6:
        row = df[df["code"] == text]
        if not row.empty:
            return text, row.iloc[0]["name"], "stock"
        # 股票表没有（如新股未收录）→ 原样返回代码
        return text, text, "stock"
    # 4) 名称匹配（先精确，再包含；名称先归一化）
    df = df.assign(norm_name=df["name"].map(_norm))
    exact = df[df["norm_name"] == text]
    if not exact.empty:
        row = exact.iloc[0]
        return row["code"], row["name"], "stock"
    contain = df[df["norm_name"].str.contains(text, case=False, regex=False)]
    if len(contain) == 1:
        row = contain.iloc[0]
        return row["code"], row["name"], "stock"
    if len(contain) > 1:
        # 有多个候选 → 返回候选列表由用户选
        cands = [(r["code"], r["name"]) for _, r in contain.head(20).iterrows()]
        return "multi", cands, "stock"
    return None


# ==================== chan.py 分析 ====================
def run_chan(df, code, kind, start, end):
    """用 chan.py 跑缠论分析，返回 (kl_list, chan)
    df: 预取的 DataFrame（dt/open/close/high/low/vol/amount）
    """
    # 数据源缓存 DataFrame（避免二次拉取）
    CYfDataSrc.set_cache(df)

    prefix = "sh" if (kind == "index" and code.startswith("000")) or (kind == "stock" and code.startswith("6")) else "sz"
    config = CChanConfig({
        "bi_strict": True,
        "seg_algo": "chan",
        "zs_combine": True,
        "divergence_rate": 0.9,
        "min_zs_cnt": 1,
        "print_warning": False,
    })
    chan = CChan(
        code=f"{prefix}.{code}",
        begin_time=start,
        end_time=end,
        data_src="custom:YfDataSrc.CYfDataSrc",
        lv_list=[KL_TYPE.K_DAY],
        config=config,
    )
    kl_list = chan[KL_TYPE.K_DAY]
    return kl_list, chan


# ==================== ECharts 交互式K线图 ====================
def ctime_str(ct):
    """CTime -> 'YYYY-MM-DD'"""
    return f"{ct.year:04d}-{ct.month:02d}-{ct.day:02d}"


def make_kline_option(df, kl_list, name, code, pt_msg, bc_msg):
    """生成 ECharts candlestick option：笔/分型/中枢/买卖点 + dataZoom 缩放"""
    dts = [d.strftime("%Y-%m-%d") for d in df["dt"]]
    kdata = [[round(r.open, 3), round(r.close, 3), round(r.low, 3), round(r.high, 3)]
             for r in df.itertuples(index=False)]

    def x_of(t_str):
        try:
            return dts.index(t_str)
        except ValueError:
            return None

    # 笔：端点连线（顶/底分型即笔端点）
    bi_line = []
    fx_bottom, fx_top = [], []
    for bi in kl_list.bi_list:
        try:
            b_klu = bi.get_begin_klu()
            e_klu = bi.get_end_klu()
        except Exception:
            continue
        xb, xe = x_of(ctime_str(b_klu.time)), x_of(ctime_str(e_klu.time))
        if xb is None or xe is None:
            continue
        bi_line.append([xb, round(bi.get_begin_val(), 3)])
        bi_line.append([xe, round(bi.get_end_val(), 3)])
        # 分型：上涨笔终点=顶分型，下跌笔终点=底分型
        if bi.dir == BI_DIR.UP:
            fx_top.append([xe, round(bi.get_end_val(), 3)])
        else:
            fx_bottom.append([xe, round(bi.get_end_val(), 3)])

    # 中枢矩形（markArea）：区间 [low, high]，时间 [begin, end]
    zs_areas = []
    for zs in kl_list.zs_list:
        x0, x1 = x_of(ctime_str(zs.begin.time)), x_of(ctime_str(zs.end.time))
        if x0 is None or x1 is None:
            continue
        zs_areas.append([{
            "xAxis": x0, "yAxis": round(zs.low, 3),
        }, {
            "xAxis": x1, "yAxis": round(zs.high, 3),
        }])

    # 买卖点标注（chan.py 原生识别）
    markers = []
    for bsp in kl_list.bs_point_lst.getSortedBspList():
        xi = x_of(ctime_str(bsp.klu.time))
        if xi is None:
            continue
        t = ",".join(x.value for x in bsp.type)
        kind = "买" if bsp.is_buy else "卖"
        markers.append({
            "name": f"{kind}{t}", "coord": [xi, round(bsp.klu.close, 3)],
            "value": f"{kind}{t} @{bsp.klu.close:.2f}",
            "itemStyle": {"color": "#14b143" if bsp.is_buy else "#ef232a"},
        })

    option = {
        "animation": False,
        "backgroundColor": "#ffffff",
        "legend": {"data": ["K线", "笔", "底分型", "顶分型"],
                   "top": 4, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "toolbox": {
            "feature": {
                "dataZoom": {"yAxisIndex": "none"},
                "restore": {},
                "saveAsImage": {},
            },
        },
        "grid": [
            {"left": 50, "right": 20, "top": 34, "height": "58%"},
            {"left": 50, "right": 20, "top": "78%", "height": "12%"},
        ],
        "xAxis": [
            {"type": "category", "data": dts, "gridIndex": 0,
             "axisLabel": {"rotate": 45, "fontSize": 10}},
            {"type": "category", "gridIndex": 1,
             "axisLabel": {"show": False}, "axisTick": {"show": False}},
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0, "splitLine": {"show": False}},
            {"gridIndex": 1, "splitNumber": 2, "axisLabel": {"show": False},
             "axisLine": {"show": False}, "axisTick": {"show": False}, "splitLine": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 60, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 60, "end": 100,
             "height": 18, "bottom": 6},
        ],
        "series": [
            {
                "name": "K线", "type": "candlestick", "data": kdata,
                "itemStyle": {"color": "#ef232a", "color0": "#14b143",
                              "borderColor": "#ef232a", "borderColor0": "#14b143"},
                "markArea": {
                    "silent": True,
                    "itemStyle": {"color": "rgba(249,168,37,0.15)", "borderColor": "#f9a825",
                                  "borderWidth": 1},
                    "data": zs_areas,
                },
                "markPoint": {
                    "symbol": "pin", "symbolSize": 42,
                    "label": {"fontSize": 9, "formatter": "{b}"},
                    "data": markers,
                },
            },
            {
                "name": "笔", "type": "line", "data": bi_line,
                "symbol": "none", "lineStyle": {"width": 1.6, "color": "#0e6efd"},
                "z": 3,
            },
            {
                "name": "底分型", "type": "scatter", "data": fx_bottom,
                "symbol": "triangle", "symbolSize": 9,
                "itemStyle": {"color": "#14b143"}, "z": 4,
            },
            {
                "name": "顶分型", "type": "scatter", "data": fx_top,
                "symbol": "triangle", "symbolRotate": 180, "symbolSize": 9,
                "itemStyle": {"color": "#ef232a"}, "z": 4,
            },
        ],
    }
    return option
