"""Recommendation data models."""

from dataclasses import asdict, dataclass, field
from typing import Any


# 描述建议所依赖数据的新鲜度、来源和错误，不代表收益确定性。
@dataclass
class DataQuality:
    nav_latest_date: str = ""
    data_source: str = ""
    is_cached: bool = False
    is_stale: bool = False
    is_simulated: bool = False
    error_message: str = ""


# 建议结果包含动作、评分、解释和风险提示，供 GUI 与报告复用。
@dataclass
class FundRecommendation:
    date: str
    fund_code: str
    fund_name: str
    fund_type: str
    category: str
    is_dca: bool
    action: str
    action_label: str
    suggested_amount: float | None
    suggested_ratio: float
    sell_signal: str
    sell_ratio: float
    sell_amount_estimate: float
    sell_reason_list: list[str]
    signal_score: float
    trend_score: float
    position_score: float
    risk_score: float
    holding_score: float
    risk_level: str
    confidence: str
    summary: str
    reason_list: list[str]
    warning_list: list[str]
    triggered_rules: list[str]
    untriggered_rules: list[str]
    indicators: dict[str, Any] = field(default_factory=dict)
    data_quality: DataQuality = field(default_factory=DataQuality)
    generated_at: str = ""

    def to_dict(self):
        return asdict(self)
