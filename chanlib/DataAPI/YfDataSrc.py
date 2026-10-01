# -*- coding: utf-8 -*-
"""
自定义数据源：akshare(东财) → yfinance 兜底
挂在 chan.py 的 DataAPI 目录下，供 data_src="custom:YfDataSrc.CYfDataSrc" 使用
"""
import datetime

from Common.CEnum import DATA_FIELD
from Common.CTime import CTime
from DataAPI.CommonStockAPI import CCommonStockApi
from KLine.KLine_Unit import CKLine_Unit


def fetch_df(code, begin, end, allow_sim=False):
    """返回 DataFrame: date/open/close/high/low/volume
    顺序: akshare东财 → yfinance兜底 → 模拟数据(仅 allow_sim=True 测试时)
    """
    df = None
    # 1) akshare 东财
    try:
        import akshare as ak
        raw = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=begin.replace("-", ""),
                                 end_date=end.replace("-", ""),
                                 adjust="qfq")
        if raw is not None and len(raw) > 0:
            import pandas as pd
            df = pd.DataFrame({
                "date": pd.to_datetime(raw["日期"]),
                "open": raw["开盘"].astype(float),
                "close": raw["收盘"].astype(float),
                "high": raw["最高"].astype(float),
                "low": raw["最低"].astype(float),
                "volume": raw["成交量"].astype(float),
            })
    except Exception as e:
        print(f"[数据源-akshare] 失败: {type(e).__name__}: {str(e)[:120]}")

    # 2) yfinance 兜底
    if df is None or len(df) == 0:
        try:
            import yfinance as yf
            sym = code
            if len(code) == 6:
                sym = code + (".SS" if code.startswith(("6", "9")) else ".SZ")
            raw = yf.download(sym, start=begin, end=end, auto_adjust=True, progress=False)
            if raw is not None and len(raw) > 0:
                import pandas as pd
                df = pd.DataFrame({
                    "date": raw.index,
                    "open": raw["Open"].astype(float),
                    "close": raw["Close"].astype(float),
                    "high": raw["High"].astype(float),
                    "low": raw["Low"].astype(float),
                    "volume": raw["Volume"].astype(float),
                })
        except Exception as e:
            print(f"[数据源-yfinance] 失败: {type(e).__name__}: {str(e)[:120]}")

    # 3) 模拟数据（仅限测试环境网络不可达时验证 API）
    if df is None or len(df) == 0 and allow_sim:
        print("[数据源] 网络不可达，使用模拟数据验证 API（仅测试）")
        import numpy as np
        import pandas as pd
        n = 700
        rng = np.random.default_rng(42)
        close = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
        open_ = close * (1 + rng.normal(0, 0.005, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
        dates = pd.bdate_range(start=begin, periods=n)
        df = pd.DataFrame({
            "date": dates,
            "open": open_,
            "close": close,
            "high": high,
            "low": low,
            "volume": rng.integers(1e5, 1e7, n).astype(float),
        })

    if df is None or len(df) == 0:
        raise RuntimeError(f"数据获取失败: {code} {begin}~{end}")
    df = df.dropna()
    df["date"] = pd.to_datetime(df["date"])
    return df.reset_index(drop=True)


class CYfDataSrc(CCommonStockApi):
    """akshare → yfinance 自定义数据源
    - 网页版：streamlit_app 先拉好 DataFrame 后通过 set_cache() 注入，避免二次拉取
    - 独立使用：无缓存时自动走 fetch_df（akshare → yfinance）
    """

    _cache_df = None

    @classmethod
    def set_cache(cls, df):
        """注入预取好的 DataFrame（date/open/close/high/low/volume 列）"""
        cls._cache_df = df

    @classmethod
    def clear_cache(cls):
        cls._cache_df = None

    def __init__(self, code, k_type, begin_date, end_date, autype):
        super().__init__(code, k_type, begin_date, end_date, autype)
        self.raw_code = code.split(".")[-1]

    def SetBasciInfo(self):
        self.name = self.code
        self.is_stock = True

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass

    def get_kl_data(self):
        df = self._cache_df
        if df is None:
            begin = str(self.begin_date)[:10] if self.begin_date else "2020-01-01"
            end = str(self.end_date)[:10] if self.end_date else datetime.date.today().strftime("%Y-%m-%d")
            df = fetch_df(self.raw_code, begin, end)
        has_date = "date" in df.columns
        for _, row in df.iterrows():
            t = row["date"] if has_date else row["dt"]
            yield CKLine_Unit({
                DATA_FIELD.FIELD_TIME: CTime(t.year, t.month, t.day, 0, 0),
                DATA_FIELD.FIELD_OPEN: float(row["open"]),
                DATA_FIELD.FIELD_CLOSE: float(row["close"]),
                DATA_FIELD.FIELD_HIGH: float(row["high"]),
                DATA_FIELD.FIELD_LOW: float(row["low"]),
                DATA_FIELD.FIELD_VOLUME: float(row["volume"] if "volume" in df.columns else row["vol"]),
            })
