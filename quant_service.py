"""Application services for indicators and backtests."""

import os

import pandas as pd

from backtest import BacktestEngine
from data_provider import build_default_provider
from fund_registry import get_fund
from indicators import calculate_fund_indicators, calculate_portfolio_indicators


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")


# 指标内部使用小数，输出数据集统一转换为百分数值。
def _percent(value):
    return round(value * 100, 4) if pd.notna(value) else None


# 应用层负责串联数据源、单基金指标和组合净值，并统一写出 CSV。
def build_indicator_dataset(holdings, provider=None):
    provider = provider or build_default_provider()
    rows, prices, weights = [], {}, {}
    total = sum(float(item.get("amount", 0)) for item in holdings)
    for holding in holdings:
        code = holding.get("code") or (get_fund(name=holding["name"]) or {}).get("code")
        if not code:
            continue
        try:
            fund_data = provider.get_fund_nav(code, refresh=False)
        except Exception:
            continue
        prices[code] = fund_data.price
        # 组合权重以分析时持仓市值为基础，之后由组合指标模块再次归一化。
        weights[code] = float(holding.get("amount", 0)) / total if total else 0
        metric = calculate_fund_indicators(fund_data.price)
        rows.append({
            "对象类型": "基金", "基金代码": code, "基金名称": holding["name"],
            "资产类别": holding.get("category", "未分类"),
            "近7日收益率": _percent(metric["return_7d"]),
            "近30日收益率": _percent(metric["return_30d"]),
            "近90日收益率": _percent(metric["return_90d"]),
            "近180日收益率": _percent(metric["return_180d"]),
            "近1年收益率": _percent(metric["return_365d"]),
            "近2年收益率": _percent(metric["return_730d"]),
            "年化收益率": _percent(metric["annual_return"]),
            "年化波动率": _percent(metric["volatility"]),
            "最大回撤": _percent(metric["max_drawdown"]),
            "夏普比率": metric["sharpe"], "卡玛比率": metric["calmar"],
            "下行波动率": _percent(metric["downside_volatility"]),
            "MA20": metric["ma20"], "MA60": metric["ma60"],
            "MA120": metric["ma120"], "MA250": metric["ma250"],
            "RSI14": metric["rsi14"], "当前回撤": _percent(metric["drawdown_now"]),
            "60日新高": metric["new_high_60d"], "60日新低": metric["new_low_60d"],
            "数据来源": fund_data.source,
        })
    portfolio_nav, portfolio_metric = calculate_portfolio_indicators(prices, weights)
    if portfolio_metric:
        rows.append({
            "对象类型": "组合", "基金代码": "PORTFOLIO", "基金名称": "当前投资组合",
            "资产类别": "组合", "年化收益率": _percent(portfolio_metric["annual_return"]),
            "年化波动率": _percent(portfolio_metric["volatility"]),
            "最大回撤": _percent(portfolio_metric["max_drawdown"]),
            "夏普比率": portfolio_metric["sharpe"], "卡玛比率": portfolio_metric["calmar"],
            "下行波动率": _percent(portfolio_metric["downside_volatility"]),
            "净值截止日期": (
                portfolio_metric["nav_latest_date"].date().isoformat()
                if portfolio_metric.get("nav_latest_date") is not None else ""
            ),
        })
    dataset = pd.DataFrame(rows)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    dataset.to_csv(os.path.join(OUTPUT_DIR, "indicator_dataset.csv"),
                   index=False, encoding="utf-8-sig")
    portfolio_nav.to_csv(os.path.join(OUTPUT_DIR, "portfolio_nav.csv"),
                         index=False, encoding="utf-8-sig")
    return dataset, portfolio_nav


# 为 GUI 提供统一回测入口，并把三种策略整理为表格和资金曲线。
def run_backtests(code, start_date=None, end_date=None, amount=100,
                  frequency="monthly", provider=None):
    provider = provider or build_default_provider()
    fund = get_fund(code=code)
    if not fund:
        raise ValueError("基金不在基金池中")
    data = provider.get_fund_nav(code, refresh=False)
    engine = BacktestEngine(data.price, start_date, end_date)
    results = [
        engine.fixed_dca(amount, frequency),
        engine.dynamic_dca(amount, frequency),
        engine.moving_average(),
    ]
    rows, curves = [], []
    for result in results:
        metric = result["metrics"]
        rows.append({
            "基金代码": code, "基金名称": fund["name"], "策略": metric["strategy"],
            "开始日期": engine.data["date"].iloc[0].date().isoformat(),
            "结束日期": engine.data["date"].iloc[-1].date().isoformat(),
            "累计投入": round(metric["total_invested"], 2),
            "期末资产": round(metric["final_value"], 2),
            "收益金额": round(metric["profit"], 2),
            "收益率": _percent(metric.get("cash_return")),
            "最大回撤": _percent(metric["max_drawdown"]),
            "夏普比率": metric["sharpe"], "卡玛比率": metric["calmar"],
            "年化波动率": _percent(metric["volatility"]),
            "交易次数": metric["trade_count"], "数据来源": data.source,
        })
        curve = result["curve"].copy()
        curve["策略"] = metric["strategy"]
        curve["基金代码"] = code
        curves.append(curve)
    summary = pd.DataFrame(rows)
    curve_df = pd.concat(curves, ignore_index=True)
    summary.to_csv(os.path.join(OUTPUT_DIR, "backtest_result.csv"),
                   index=False, encoding="utf-8-sig")
    curve_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_curve.csv"),
                    index=False, encoding="utf-8-sig")
    return summary, curve_df
