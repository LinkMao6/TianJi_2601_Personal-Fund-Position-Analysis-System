"""Portfolio NAV and portfolio-level risk indicators."""

import pandas as pd

from .fund_indicators import normalize_nav, performance_metrics


# 将各基金净值标准化后按当前权重合成为组合净值。
def calculate_portfolio_indicators(fund_prices, weights):
    frames = []
    latest_dates = {}
    for code, price in fund_prices.items():
        nav = normalize_nav(price).set_index("date")["nav"]
        if nav.empty:
            continue
        # 每只基金以首日净值归一为 1，消除绝对净值尺度差异。
        frames.append(nav.rename(code) / nav.iloc[0])
        latest_dates[code] = nav.index.max()
    if not frames:
        return pd.DataFrame(), {}
    aligned = pd.concat(frames, axis=1).sort_index().ffill()
    active = [code for code in aligned.columns if code in weights]
    if not active:
        return pd.DataFrame(), {}
    # 组合只能计算到所有有效成分共同拥有的最新正式净值日。
    # 若继续向后填充披露滞后的基金，会把其未知收益错误地当成 0%。
    common_end = min(latest_dates[code] for code in active)
    aligned = aligned.loc[:common_end, active].dropna()
    if aligned.empty:
        return pd.DataFrame(), {}
    # 仅对实际存在净值和权重的基金重新归一化权重。
    normalized_weights = pd.Series({code: weights[code] for code in active}, dtype=float)
    if normalized_weights.sum() <= 0:
        return pd.DataFrame(), {}
    normalized_weights /= normalized_weights.sum()
    portfolio_nav = aligned[active].mul(normalized_weights, axis=1).sum(axis=1)
    result = pd.DataFrame({"date": portfolio_nav.index, "portfolio_nav": portfolio_nav.values})
    metrics = performance_metrics(portfolio_nav)
    metrics["asset_count"] = len(active)
    metrics["nav_latest_date"] = common_end
    return result, metrics
