"""Explainable recommendation scoring."""

import math


# 将缺失值和非数字安全转换为默认值，避免单项指标中断整条建议。
def _number(value, default=0.0):
    try:
        value = float(value)
        return default if math.isnan(value) else value
    except (TypeError, ValueError):
        return default


# 趋势评分关注当前净值与中长期均线的相对关系及 MA120 斜率。
def score_trend(indicators):
    nav = _number(indicators.get("current_nav"))
    ma20, ma60 = _number(indicators.get("ma20")), _number(indicators.get("ma60"))
    ma120, ma250 = _number(indicators.get("ma120")), _number(indicators.get("ma250"))
    score, reasons = 50.0, []
    if nav > ma20 > ma60 > ma120 > 0:
        score, reasons = 90, ["净值及中短期均线呈多头排列"]
    elif nav > ma120 > 0:
        score, reasons = 72, ["当前净值位于MA120上方"]
    elif nav < ma120 and nav > ma250 > 0:
        score, reasons = 50, ["长期趋势尚存，但中期趋势偏弱"]
    elif ma250 > 0 and nav < ma250 and ma60 < ma120:
        score, reasons = 20, ["净值低于MA250且MA60低于MA120"]
    slope = _number(indicators.get("ma120_slope"))
    score += 6 if slope > 0.02 else -6 if slope < -0.02 else 0
    return max(0, min(100, score)), reasons


# 位置评分结合近252日回撤和 RSI，避免仅因下跌就机械加仓。
def score_position(indicators):
    dd = _number(indicators.get("dd252"))
    rsi = _number(indicators.get("rsi14"), 50)
    if dd >= -0.05:
        score, reasons = 48, ["接近近252日高位，追涨空间有限"]
    elif dd >= -0.15:
        score, reasons = 78, ["处于近252日适中回撤区间"]
    elif dd >= -0.25:
        score, reasons = 70, ["回撤较深，适合分批观察而非一次重仓"]
    else:
        score, reasons = 38, ["回撤超过25%，需先确认趋势是否企稳"]
    if rsi > 80:
        score -= 28
        reasons.append("RSI超过80，短期明显过热")
    elif rsi > 75:
        score -= 18
        reasons.append("RSI超过75，短期偏热")
    elif rsi < 35:
        score += 8
        reasons.append("RSI低于35，处于超跌观察区")
    return max(0, min(100, score)), reasons


# 风险评分综合夏普、最大回撤和波动率；债券基金使用更严格阈值。
def score_risk(indicators, conservative=False):
    sharpe = _number(indicators.get("sharpe"), -1)
    drawdown = _number(indicators.get("max_drawdown"), 1)
    volatility = _number(indicators.get("volatility"), 1)
    score = 85 if sharpe > 1 else 70 if sharpe >= 0.5 else 55 if sharpe >= 0 else 25
    drawdown_limit = 0.08 if conservative else 0.25
    if drawdown > drawdown_limit:
        score -= min(30, (drawdown - drawdown_limit) * 100)
    vol_limit = 0.08 if conservative else 0.30
    if volatility > vol_limit:
        score -= min(25, (volatility - vol_limit) * 100)
    return max(0, min(100, score)), [
        f"夏普比率{sharpe:.2f}",
        f"历史最大回撤{drawdown:.1%}",
        f"年化波动率{volatility:.1%}",
    ]


# 持仓评分将组合权重和实际盈亏纳入建议，限制单基金过度集中。
def score_holding(holding, indicators):
    weight = _number(holding.get("portfolio_weight"))
    profit_rate = _number(holding.get("holding_profit_rate"))
    rsi = _number(indicators.get("rsi14"), 50)
    trend_bad = indicators.get("price_above_ma250") is False and indicators.get("ma60_above_ma120") is False
    score, reasons = 70.0, []
    if weight > 0.30:
        score -= 28
        reasons.append("组合仓位超过30%，继续加仓需谨慎")
    elif weight > 0.20:
        score -= 15
        reasons.append("组合仓位偏高")
    else:
        reasons.append("当前组合仓位未明显过高")
    if profit_rate > 0.25 and rsi > 75:
        score -= 15
        reasons.append("持仓盈利较高且短期过热")
    if profit_rate < -0.20 and trend_bad:
        score -= 20
        reasons.append("亏损较大且长期趋势走弱")
    return max(0, min(100, score)), reasons


# 四维评分是可解释规则加权，不是机器学习预测或收益承诺。
def calculate_scores(indicators, holding, conservative=False, trend_weight=0.30):
    trend, trend_reasons = score_trend(indicators)
    position, position_reasons = score_position(indicators)
    risk, risk_reasons = score_risk(indicators, conservative)
    holding_score, holding_reasons = score_holding(holding, indicators)
    if conservative:
        total = trend * 0.15 + position * 0.20 + risk * 0.45 + holding_score * 0.20
    else:
        total = trend * trend_weight + position * 0.25 + risk * 0.25 + holding_score * 0.20
    return {
        "total": round(total, 2), "trend": round(trend, 2),
        "position": round(position, 2), "risk": round(risk, 2),
        "holding": round(holding_score, 2),
        "reasons": trend_reasons + position_reasons + risk_reasons + holding_reasons,
    }
