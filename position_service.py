"""Unified position recalculation and portfolio summary."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from app_logging import get_logger
from fund_registry import get_fund

logger = get_logger(__name__)

# 单基金估值的统一输出结构，供持仓表、CSV 和建议引擎共享。
@dataclass
class PositionSummary:
    fund_code: str
    fund_name: str
    category: str
    latest_nav: float | None
    latest_nav_date: str
    total_units: float | None
    current_value: float | None
    total_cost: float
    profit_amount: float | None
    profit_rate: float | None
    data_quality: str
    valuation_method: str
    data_source: str
    cache_used: bool
    stale: bool
    warning_list: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# 从净值表中提取最后一个有效单位净值及其日期。
def _latest_nav(fund_data):
    price = fund_data.price.copy()
    price["date"] = pd.to_datetime(price["date"])
    price["netvalue"] = pd.to_numeric(price["netvalue"], errors="coerce")
    price = price.dropna(subset=["date", "netvalue"]).sort_values("date")
    if price.empty or float(price["netvalue"].iloc[-1]) <= 0:
        raise ValueError("最新单位净值无效")
    return (
        float(price["netvalue"].iloc[-1]),
        price["date"].iloc[-1].date().isoformat(),
        len(price),
    )


# 所有持仓统一从此处重算，避免不同页面各自解释旧 amount/profit 字段。
def recalculate_positions(data, provider, refresh=True):
    """Recalculate all positions from units, latest NAV and stored cost."""
    positions = []
    now = datetime.now()
    for holding in data.get("holdings", []):
        code = holding.get("code", "")
        name = holding.get("name", code)
        category = holding.get("category", "未分类")
        manual_amount = float(holding.get("manual_amount", holding.get("amount", 0)))
        manual_profit = float(holding.get("manual_profit", holding.get("profit", 0)))
        total_cost = float(
            holding.get("total_cost", manual_amount - manual_profit)
        )
        warnings = []
        registered = get_fund(code=code)
        # 基金代码是关联主键，名称不一致时保留数据并给出质量警告。
        if registered and registered.get("name") != name:
            warnings.append(
                f"持仓名称与基金池不一致：基金池为“{registered.get('name')}”"
            )
        try:
            fund_data = provider.get_fund_nav(code, refresh=refresh)
            latest_nav, latest_date, nav_rows = _latest_nav(fund_data)
            age_days = (now.date() - pd.Timestamp(latest_date).date()).days
            stale_limit = 7 if "QDII" in name.upper() else 4
            stale = age_days > stale_limit
            if stale:
                warnings.append(f"最新净值距今{age_days}天，数据可能过期")

            recorded_units = float(holding.get("units", 0))
            valuation_units = float(holding.get("valuation_units", 0))
            unit_source = holding.get("valuation_unit_source", "")
            # 真实交易份额优先；旧数据只有估算份额时必须明确标记为估算。
            if recorded_units > 0 and unit_source == "transaction_units":
                total_units = recorded_units
                method, quality = "precise", "精确"
            elif valuation_units > 0:
                total_units = valuation_units
                method, quality = "estimated", "估算"
                warnings.append("份额由旧持仓市值估算，并非真实确认份额")
            elif manual_amount > 0:
                # 历史持仓缺少份额时，仅建立可追踪的估值基准。
                total_units = manual_amount / latest_nav
                method, quality = "estimated", "估算"
                holding["valuation_units"] = round(total_units, 8)
                holding["legacy_estimated_units"] = round(total_units, 8)
                holding["valuation_unit_source"] = "inferred_from_previous_value"
                warnings.append("缺少确认份额，已按旧持仓市值和最新净值估算")
            else:
                total_units = 0.0
                method, quality = "manual_fallback", "旧数据"

            transaction_units = float(holding.get("transaction_units", 0))
            legacy_units = float(holding.get("legacy_estimated_units", 0))
            # 新交易份额可与尚未修复的历史估算份额并存。
            if transaction_units > 0 or legacy_units > 0:
                total_units = transaction_units + legacy_units
                holding["valuation_units"] = round(total_units, 8)
                if legacy_units > 0:
                    method, quality = "estimated", "估算"
                else:
                    method, quality = "precise", "精确"
                    holding["valuation_unit_source"] = "transaction_units"
            # 市值随最新净值变化，成本来自未卖出批次，两者差额即当前收益。
            current_value = round(total_units * latest_nav, 2)
            profit_amount = round(current_value - total_cost, 2)
            profit_rate = profit_amount / total_cost if total_cost > 0 else None
            position = PositionSummary(
                code, name, category, latest_nav, latest_date, total_units,
                current_value, total_cost, profit_amount, profit_rate, quality,
                method, fund_data.source, bool(fund_data.stale), stale, warnings,
            )
            holding.update({
                "amount": current_value,
                "profit": profit_amount,
                "current_value": current_value,
                "profit_amount": profit_amount,
                "profit_rate": profit_rate,
                "latest_nav": round(latest_nav, 6),
                "latest_nav_date": latest_date,
                "valuation_method": method,
                "data_quality": quality,
                "valuation_source": fund_data.source,
                "valuation_updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "valuation_warning_list": warnings,
                "nav_rows": nav_rows,
            })
            logger.info(
                "position_nav code=%s name=%s latest_nav=%s latest_date=%s "
                "nav_rows=%s source=%s cache_used=%s stale=%s warnings=%s",
                code, name, latest_nav, latest_date, nav_rows, fund_data.source,
                fund_data.stale, stale, warnings,
            )
        except Exception as exc:
            # 数据源失败时保留旧金额并显式标记失败，不生成随机估值。
            fallback = manual_amount if manual_amount > 0 else None
            profit = fallback - total_cost if fallback is not None else None
            rate = profit / total_cost if profit is not None and total_cost > 0 else None
            warnings.append(f"净值读取失败：{exc}")
            position = PositionSummary(
                code, name, category, None, "", None, fallback, total_cost,
                profit, rate, "读取失败", "manual_fallback" if fallback is not None else "unavailable",
                "", False, True, warnings,
            )
            holding.update({
                "current_value": fallback, "profit_amount": profit,
                "profit_rate": rate, "valuation_method": position.valuation_method,
                "data_quality": position.data_quality,
                "valuation_warning_list": warnings,
            })
            logger.error(
                "position_nav code=%s name=%s source=none cache_used=false "
                "stale=true error=%s", code, name, exc
            )
        positions.append(position)

    # 组合摘要只汇总当前仍有市值的持仓。
    active = [p for p in positions if (p.current_value or 0) > 0]
    total_value = sum(p.current_value or 0 for p in active)
    total_cost = sum(p.total_cost for p in active)
    total_profit = total_value - total_cost
    portfolio = {
        "total_current_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "total_profit_rate": total_profit / total_cost if total_cost > 0 else None,
        "profitable_count": sum((p.profit_amount or 0) > 0 for p in active),
        "loss_count": sum((p.profit_amount or 0) < 0 for p in active),
        "holding_count": len(active),
        "stale_data_count": sum(p.stale for p in active),
        "estimated_data_count": sum(p.valuation_method == "estimated" for p in active),
        "failed_data_count": sum(p.data_quality == "读取失败" for p in active),
    }
    return positions, portfolio
