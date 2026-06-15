"""Single-fund return, risk and trend indicators."""

import numpy as np
import pandas as pd


# 指标统一优先使用累计净值，以尽量反映历史分红影响。
def normalize_nav(price):
    df = price.copy()
    df["date"] = pd.to_datetime(df["date"])
    value_col = "totvalue" if "totvalue" in df.columns else "netvalue"
    df["nav"] = pd.to_numeric(df[value_col], errors="coerce")
    return df[["date", "nav"]].dropna().drop_duplicates("date").sort_values("date")


# 月度和年度区间使用日历偏移，而不是简单按固定天数近似。
CALENDAR_PERIODS = {
    30: pd.DateOffset(months=1),
    90: pd.DateOffset(months=3),
    180: pd.DateOffset(months=6),
    365: pd.DateOffset(years=1),
    730: pd.DateOffset(years=2),
}


# 目标日期无净值时取该日期之前最近交易日，避免使用未来信息。
def _period_return(df, days):
    if df.empty:
        return np.nan
    end_date = df["date"].iloc[-1]
    cutoff = end_date - CALENDAR_PERIODS.get(days, pd.Timedelta(days=days))
    before = df[df["date"] <= cutoff]
    if before.empty:
        return np.nan
    return df["nav"].iloc[-1] / before["nav"].iloc[-1] - 1


# 将净值序列转换为可比较的收益与风险统计口径。
def performance_metrics(nav, risk_free=0.02):
    nav = nav.dropna()
    returns = nav.pct_change().dropna()
    if len(nav) < 2 or returns.empty:
        return {key: np.nan for key in (
            "total_return", "annual_return", "volatility", "max_drawdown",
            "sharpe", "calmar", "downside_volatility",
        )}
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    # 按真实自然日跨度做复合年化，不假设样本恰好覆盖整数年。
    annual = (1 + total) ** (1 / years) - 1 if total > -1 else -1
    # 基金交易日收益率按一年 252 个交易日进行波动率年化。
    volatility = returns.std() * np.sqrt(252)
    drawdown = nav / nav.cummax() - 1
    max_drawdown = abs(drawdown.min())
    sharpe = (annual - risk_free) / volatility if volatility > 0 else np.nan
    calmar = annual / max_drawdown if max_drawdown > 0 else np.nan
    downside = returns[returns < 0].std() * np.sqrt(252)
    return {
        "total_return": total, "annual_return": annual, "volatility": volatility,
        "max_drawdown": max_drawdown, "sharpe": sharpe, "calmar": calmar,
        "downside_volatility": downside,
    }


# 汇总单基金阶段收益、风险、均线、RSI 和位置类指标。
def calculate_fund_indicators(price):
    df = normalize_nav(price)
    series = df.set_index("date")["nav"]
    metrics = performance_metrics(series)
    for days in (7, 20, 30, 60, 90, 120, 180, 250, 365, 730):
        metrics[f"return_{days}d"] = _period_return(df, days)
    for window in (20, 60, 120, 250):
        metrics[f"ma{window}"] = series.rolling(window).mean().iloc[-1] if len(series) >= window else np.nan
    delta = series.diff()
    # RSI14 使用 14 个交易日平均上涨与平均下跌幅度衡量短期强弱。
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0)
    metrics["rsi14"] = rsi.iloc[-1] if len(rsi) else np.nan
    metrics["current_nav"] = series.iloc[-1] if not series.empty else np.nan
    metrics["drawdown_now"] = 1 - series.iloc[-1] / series.cummax().iloc[-1] if not series.empty else np.nan
    metrics["new_high_60d"] = bool(series.iloc[-1] >= series.tail(60).max()) if not series.empty else False
    metrics["new_low_60d"] = bool(series.iloc[-1] <= series.tail(60).min()) if not series.empty else False
    metrics["dd252"] = (
        series.iloc[-1] / series.tail(252).max() - 1 if not series.empty else np.nan
    )
    metrics["new_low_5d"] = (
        bool(series.iloc[-1] <= series.tail(5).min()) if not series.empty else False
    )
    for window in (20, 60, 120, 250):
        # 均线窗口均按有效净值记录数，即近似交易日口径计算。
        metrics[f"price_above_ma{window}"] = (
            bool(series.iloc[-1] > metrics[f"ma{window}"])
            if pd.notna(metrics[f"ma{window}"]) else None
        )
    metrics["ma60_above_ma120"] = (
        bool(metrics["ma60"] > metrics["ma120"])
        if pd.notna(metrics["ma60"]) and pd.notna(metrics["ma120"]) else None
    )
    ma120_series = series.rolling(120).mean()
    metrics["ma120_slope"] = (
        ma120_series.iloc[-1] / ma120_series.iloc[-21] - 1
        if len(ma120_series.dropna()) >= 21 and ma120_series.iloc[-21] != 0 else np.nan
    )
    metrics["nav_latest_date"] = df["date"].iloc[-1] if not df.empty else None
    metrics["observation_count"] = len(df)
    return metrics
