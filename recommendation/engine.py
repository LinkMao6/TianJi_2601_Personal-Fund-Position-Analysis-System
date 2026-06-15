"""Rules converting metrics and scores into explainable actions."""

from datetime import datetime

from app_info import DISCLAIMER
from .models import FundRecommendation
from .scoring import calculate_scores


ACTION_LABELS = {
    "strong_buy": "可重点关注 / 可加仓", "buy": "可小额买入",
    "hold": "继续持有", "watch": "观察", "reduce": "适当减仓",
    "sell_partial": "部分卖出", "avoid": "暂不建议买入",
    "insufficient": "数据不足，无法判断",
    "strong_increase_dca": "强增强定投", "increase_dca": "轻增强定投",
    "continue_dca": "正常定投", "reduce_dca": "减半定投",
    "pause_dca": "暂停定投", "resume_dca": "恢复定投",
    "stop_dca_watch": "停止定投并观察",
}


# 基金类型决定风险阈值和特殊规则，名称仅作为配置缺失时的辅助识别。
def _fund_kind(fund):
    text = f"{fund.get('fund_type', '')} {fund.get('category', '')} {fund.get('name', '')}".lower()
    if fund.get("fund_type") == "money" or "货币" in text or "现金" in text:
        return "money"
    if "债" in text:
        return "bond"
    if "黄金" in text or "商品" in text:
        return "commodity"
    if "qdii" in text:
        return "qdii"
    if fund.get("fund_type") == "normal" and fund.get("category") == "未分类":
        return "unknown"
    return "equity"


# 风险等级用于界面提示，不替代基金正式风险评级。
def _risk_level(indicators, kind):
    if kind == "money":
        return "低"
    vol = float(indicators.get("volatility") or 0)
    dd = float(indicators.get("max_drawdown") or 0)
    return "高" if vol > 0.30 or dd > 0.35 else "中" if vol > 0.12 or dd > 0.15 else "低"


# 卖出信号只在持仓、趋势和风险条件共同满足时提示。
def _sell_signal(holding, indicators, score):
    amount = float(holding.get("holding_amount", 0))
    if amount <= 0:
        return "none", 0, []
    profit_rate = float(holding.get("holding_profit_rate", 0))
    bad_trend = indicators.get("price_above_ma250") is False and indicators.get("ma60_above_ma120") is False
    rsi = float(indicators.get("rsi14") or 50)
    reasons = []
    if profit_rate > 0.25 and rsi > 78:
        return "take_profit", 0.2, ["持仓盈利较高且RSI进入过热区"]
    if bad_trend and score < 30:
        reasons.append("长期均线趋势走弱且综合评分低")
        return "risk_reduce", 0.3, reasons
    if profit_rate < -0.25 and bad_trend and float(indicators.get("sharpe") or -1) < 0:
        return "stop_loss_warning", 0.2, ["亏损较深、趋势走弱且风险收益指标为负"]
    return "none", 0, []


# 将历史指标、当前持仓和基金配置转换为可解释规则结果。
def generate_recommendation(fund, indicators, holding, quality):
    now = datetime.now()
    base = dict(
        date=now.date().isoformat(), fund_code=fund["code"], fund_name=fund["name"],
        fund_type=fund.get("fund_type", "unknown"), category=fund.get("category", "未分类"),
        is_dca=bool(fund.get("is_dca", False)), generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    warnings = [DISCLAIMER]
    # 数据失败、模拟或明显过期时停止生成方向性金额建议。
    if quality.error_message or quality.is_simulated or quality.is_stale:
        reason = quality.error_message or "净值数据已过期"
        warnings.append(reason)
        return FundRecommendation(
            **base, action="insufficient", action_label=ACTION_LABELS["insufficient"],
            suggested_amount=None, suggested_ratio=0, sell_signal="none", sell_ratio=0,
            sell_amount_estimate=0, sell_reason_list=[], signal_score=0, trend_score=0,
            position_score=0, risk_score=0, holding_score=0, risk_level="未知",
            confidence="低", summary=f"数据质量不足：{reason}", reason_list=[reason],
            warning_list=warnings, triggered_rules=["数据质量检查未通过"],
            untriggered_rules=[], indicators=indicators, data_quality=quality,
        )
    kind = _fund_kind(fund)
    if kind == "money":
        # 货币基金以流动性管理为主，不使用均线和 RSI 择时。
        return FundRecommendation(
            **base, action="hold", action_label="按流动性需要配置", suggested_amount=0,
            suggested_ratio=0, sell_signal="none", sell_ratio=0, sell_amount_estimate=0,
            sell_reason_list=[], signal_score=60, trend_score=0, position_score=0,
            risk_score=85, holding_score=60, risk_level="低", confidence="中",
            summary="现金管理工具，建议根据流动性需要配置。",
            reason_list=["货币基金不使用均线和RSI进行择时"],
            warning_list=warnings, triggered_rules=["货币基金现金管理规则"],
            untriggered_rules=[], indicators=indicators, data_quality=quality,
        )
    scores = calculate_scores(indicators, holding, conservative=kind == "bond")
    total = scores["total"]
    rsi = float(indicators.get("rsi14") or 50)
    bad_trend = indicators.get("price_above_ma250") is False and indicators.get("ma60_above_ma120") is False
    triggered, untriggered = [], []
    suggested_amount, suggested_ratio = 0.0, 0.0
    if fund.get("is_dca"):
        # 定投金额必须来自用户配置，系统不会自行推测可投入预算。
        base_amount = float(fund.get("dca_base_amount", 0))
        if base_amount <= 0:
            quality.error_message = "定投基础金额未配置或不大于0"
            warnings.append(quality.error_message)
            return FundRecommendation(
                **base, action="insufficient", action_label=ACTION_LABELS["insufficient"],
                suggested_amount=None, suggested_ratio=0, sell_signal="none", sell_ratio=0,
                sell_amount_estimate=0, sell_reason_list=[], signal_score=total,
                trend_score=scores["trend"], position_score=scores["position"],
                risk_score=scores["risk"], holding_score=scores["holding"],
                risk_level=_risk_level(indicators, kind), confidence="低",
                summary="定投配置不完整，无法生成金额建议。",
                reason_list=["请在基金池中设置大于0的定投基础金额"],
                warning_list=warnings, triggered_rules=["定投配置检查未通过"],
                untriggered_rules=[], indicators=indicators, data_quality=quality,
            )
        max_multi = float(fund.get("dca_max_multiplier", 2))
        dd = float(indicators.get("dd252") or 0)
        # 增强定投要求回撤、趋势、评分和用户权限同时满足。
        if (-0.20 <= dd <= -0.08 and indicators.get("price_above_ma250")
                and not indicators.get("new_low_5d") and rsi < 65 and total >= 70
                and fund.get("dca_allow_increase", True)):
            action, suggested_ratio = "strong_increase_dca", min(2, max_multi)
            triggered.append("回撤适中、长期趋势有效且允许增强定投")
        elif (-0.20 <= dd <= -0.05 and indicators.get("price_above_ma250")
              and total >= 60 and fund.get("dca_allow_increase", True)):
            action, suggested_ratio = "increase_dca", min(1.5, max_multi)
            triggered.append("处于分批投入区间且综合评分不低于60")
        elif ((bad_trend and total < 35) or (rsi > 80 and indicators.get("price_above_ma250"))
              or quality.is_stale) and fund.get("dca_allow_pause", True):
            action, suggested_ratio = "pause_dca", 0
            triggered.append("趋势恶化、极端过热或数据质量触发暂停")
        elif rsi > 75 or bad_trend or 30 <= total < 45:
            action, suggested_ratio = "reduce_dca", 0.5
            triggered.append("过热或中长期趋势偏弱，降低定投强度")
        else:
            action, suggested_ratio = "continue_dca", 1.0
            triggered.append("未触发增强、减量或暂停条件")
        if fund["code"] == "040046" and action == "pause_dca" and rsi <= 85:
            action, suggested_ratio = "reduce_dca", 0.5
            triggered.append("华安核心底仓规则：非极端过热优先减量而非暂停")
        suggested_amount = round(base_amount * suggested_ratio, 2)
        all_rules = {
            "strong_increase_dca": "强增强定投条件",
            "increase_dca": "轻增强定投条件",
            "continue_dca": "正常定投条件",
            "reduce_dca": "减半定投条件",
            "pause_dca": "暂停定投条件",
        }
        untriggered.extend(
            label for key, label in all_rules.items() if key != action
        )
    else:
        # 普通基金按综合评分区间映射动作，并用 RSI 防止高位追涨。
        if total >= 80:
            action = "watch" if rsi > 75 else "strong_buy"
        elif total >= 65:
            action = "buy"
        elif total >= 45:
            action = "hold" if holding.get("holding_amount", 0) else "watch"
        elif total >= 30:
            action = "reduce" if holding.get("portfolio_weight", 0) > 0.25 and bad_trend else "watch"
        else:
            action = "sell_partial" if holding.get("holding_amount", 0) and bad_trend else "avoid"
        triggered.append(f"综合评分{total:.1f}对应普通基金动作区间")
        untriggered.extend([
            "未同时满足更高评分动作区间",
            "未仅因持仓亏损触发卖出",
        ])
    sell_signal, sell_ratio, sell_reasons = _sell_signal(holding, indicators, total)
    # 卖出金额仅是风险提示对应的估算值，不会触发自动交易。
    sell_amount = round(float(holding.get("holding_amount", 0)) * sell_ratio, 2)
    if kind == "qdii":
        warnings.append("QDII基金净值可能滞后一个或多个交易日。")
    if kind == "commodity":
        scores["reasons"].append("黄金/商品资产可提供一定组合分散作用，但不保证降低全部风险")
    confidence = "高" if quality.data_source == "xalpha" and not quality.is_cached else "中"
    if kind == "unknown":
        confidence = "低"
        warnings.append("基金类型未明确，使用保守通用规则。")
    risk_level = _risk_level(indicators, kind)
    summary = f"{ACTION_LABELS[action]}，综合评分{total:.1f}，风险等级{risk_level}。"
    return FundRecommendation(
        **base, action=action, action_label=ACTION_LABELS[action],
        suggested_amount=suggested_amount, suggested_ratio=suggested_ratio,
        sell_signal=sell_signal, sell_ratio=sell_ratio, sell_amount_estimate=sell_amount,
        sell_reason_list=sell_reasons, signal_score=total, trend_score=scores["trend"],
        position_score=scores["position"], risk_score=scores["risk"],
        holding_score=scores["holding"], risk_level=risk_level, confidence=confidence,
        summary=summary, reason_list=scores["reasons"], warning_list=warnings,
        triggered_rules=triggered, untriggered_rules=untriggered,
        indicators=indicators, data_quality=quality,
    )
