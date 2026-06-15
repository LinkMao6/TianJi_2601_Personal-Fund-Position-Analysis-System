"""Recommendation service, persistence and report export."""

import csv
import os
from datetime import datetime

from app_info import APP_FULL_NAME
from data_provider import DataProviderError, build_default_provider
from fund_registry import get_fund, list_funds
from indicators import calculate_fund_indicators
from portfolio_store import load_data, lot_rows

from .engine import DISCLAIMER, generate_recommendation
from .models import DataQuality


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(PROJECT_DIR, "data", "history")
HISTORY_FILE = os.path.join(HISTORY_DIR, "fund_recommendations.csv")
REPORT_FILE = os.path.join(PROJECT_DIR, "output", "fund_recommendation_report.md")


# 服务层负责准备数据、调用规则引擎、保存历史并导出报告。
class RecommendationService:
    def __init__(self, provider=None):
        self.provider = provider or build_default_provider()

    @staticmethod
    def _holding_context(code):
        # 建议引擎读取统一估值字段，避免使用分析前的旧持仓金额。
        data = load_data()
        holding = next((item for item in data["holdings"] if item.get("code") == code), {})
        total = sum(
            float(item.get("current_value", item.get("amount", 0)) or 0)
            for item in data["holdings"]
        )
        amount = float(holding.get("current_value", holding.get("amount", 0)) or 0)
        cost = float(holding.get("total_cost", amount - float(holding.get("profit", 0))))
        lots = [row for row in lot_rows(data) if row["基金代码"] == code]
        days = [row["持有天数"] for row in lots if isinstance(row["持有天数"], int)]
        return {
            "holding_amount": amount, "holding_cost": cost,
            "holding_profit": float(holding.get("profit_amount", amount - cost)),
            "holding_profit_rate": (
                holding.get("profit_rate")
                if holding.get("profit_rate") is not None
                else ((amount - cost) / cost if cost else 0)
            ),
            "portfolio_weight": amount / total if total else 0,
            "holding_days": max(days) if days else None,
            "redeem_fee_estimate": None,
        }

    def generate_recommendation_for_fund(self, fund_code, refresh_data=False):
        fund = get_fund(code=fund_code)
        if not fund:
            raise ValueError(f"基金 {fund_code} 不在基金池中")
        try:
            result = self.provider.get_fund_nav(fund_code, refresh=refresh_data)
            indicators = calculate_fund_indicators(result.price)
            latest = indicators.get("nav_latest_date")
            latest_text = latest.date().isoformat() if latest is not None else ""
            age = (datetime.now().date() - latest.date()).days if latest is not None else 9999
            # QDII 公布净值通常更慢，因此允许更宽的日期滞后阈值。
            stale_limit = 7 if "QDII" in fund.get("name", "").upper() else 4
            quality = DataQuality(
                nav_latest_date=latest_text, data_source=result.source,
                is_cached=result.stale, is_stale=age > stale_limit,
                is_simulated=False,
                error_message=f"净值数据距今{age}天" if age > stale_limit else "",
            )
        except Exception as exc:
            indicators = {}
            quality = DataQuality(error_message=str(exc))
        return generate_recommendation(
            fund, indicators, self._holding_context(fund_code), quality
        )

    def generate_recommendations(self, fund_codes=None, only_holdings=False,
                                 refresh_data=False):
        # only_holdings 用于只评估当前有市值的基金。
        if only_holdings:
            fund_codes = [
                item.get("code") for item in load_data()["holdings"]
                if float(item.get("current_value", item.get("amount", 0)) or 0) > 0
                and item.get("code")
            ]
        fund_codes = fund_codes or [fund["code"] for fund in list_funds(enabled_only=True)]
        recommendations = [
            self.generate_recommendation_for_fund(code, refresh_data)
            for code in dict.fromkeys(fund_codes)
        ]
        self.save_recommendations(recommendations)
        return recommendations

    def save_recommendations(self, recommendations):
        # 建议历史采用追加写入，便于观察同一基金规则结果的变化。
        os.makedirs(HISTORY_DIR, exist_ok=True)
        fields = [
            "date", "fund_code", "fund_name", "is_dca", "action", "action_label",
            "suggested_amount", "suggested_ratio", "sell_signal", "sell_ratio",
            "signal_score", "risk_level", "confidence", "summary",
            "nav_latest_date", "data_source", "generated_at",
        ]
        exists = os.path.exists(HISTORY_FILE)
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            if not exists:
                writer.writeheader()
            for rec in recommendations:
                row = {key: getattr(rec, key) for key in fields if hasattr(rec, key)}
                row["nav_latest_date"] = rec.data_quality.nav_latest_date
                row["data_source"] = rec.data_quality.data_source
                writer.writerow(row)
        return HISTORY_FILE

    def export_recommendation_report(self, recommendations):
        # 报告保留规则解释和风险提示，不输出自动交易指令。
        os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
        lines = [f"# {APP_FULL_NAME} - 策略建议报告", "", f"> {DISCLAIMER}", "",
                 f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        for rec in recommendations:
            lines.extend([
                f"## {rec.fund_name}（{rec.fund_code}）", "",
                f"- 当前建议：{rec.action_label}",
                f"- 建议金额：{rec.suggested_amount if rec.suggested_amount is not None else 'N/A'}",
                f"- 卖出信号：{rec.sell_signal}，比例 {rec.sell_ratio:.0%}",
                f"- 信号分：{rec.signal_score:.1f}",
                f"- 风险等级 / 置信度：{rec.risk_level} / {rec.confidence}",
                f"- 数据日期 / 来源：{rec.data_quality.nav_latest_date} / {rec.data_quality.data_source}",
                "", "建议原因：",
            ])
            lines.extend([f"- {reason}" for reason in rec.reason_list])
            lines.extend(["", "风险提示："])
            lines.extend([f"- {warning}" for warning in rec.warning_list])
            lines.append("")
        with open(REPORT_FILE, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))
        return REPORT_FILE
