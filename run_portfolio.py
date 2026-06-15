# -*- coding: utf-8 -*-
"""
xalpha 基金组合分析 — 快速运行脚本
====================================
在终端执行:  python run_portfolio.py
即可一键完成全部分析并输出图表与报告。

提供 run_full_analysis() 函数，支持进度回调和日志回调，供 UI 调用。
"""

import os
import sys
import traceback
from app_info import APP_FULL_NAME
from fund_registry import list_funds
from portfolio_store import (
    load_data, portfolio_tuples, position_rows, update_holdings_market_values,
)
from quant_service import build_indicator_dataset, run_backtests
# 确保能找到 portfolio_analysis 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portfolio_analysis import (
    analyze_asset_allocation,
    analyze_returns,
    analyze_netvalue,
    analyze_risk,
    plot_category_return,
    plot_risk_comparison,
    run_fixed_investment_demo,
    save_summary_csv,
    save_holdings_csv,
    save_period_return_csv,
    save_dca_csv,
    generate_markdown_report,
    OUTPUT_DIR,
    DATA_DIR,
)


# GUI 与命令行共用的完整分析入口，统一编排数据、计算和输出。
def run_full_analysis(progress_callback=None, log_callback=None, portfolio=None):
    """
    执行完整的基金组合分析。

    参数:
        progress_callback: callable(int) — 接收 0-100 的进度百分比
        log_callback: callable(str) — 接收中文日志消息

    返回:
        dict: result_paths，包含所有生成文件的路径，键名如下：
            - report:          portfolio_report.md
            - risk_report:     output/risk_report.csv
            - summary:         output/summary.csv
            - holdings_result: output/holdings_result.csv
            - period_return:   output/period_return.csv
            - dca_result:      output/dca_result.csv
            - asset_allocation: output/asset_allocation.png
            - category_return:  output/category_return.png
            - risk_comparison:  output/risk_comparison.png
        如果某文件生成失败，对应值为 None。
    """
    # 回调辅助
    # GUI 将消息放入线程安全队列，命令行则直接输出。
    def _log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    def _progress(pct):
        if progress_callback:
            progress_callback(pct)

    result_paths = {}
    errors = []
    result_data = {
        "summary": None,
        "period_returns": None,
        "risk": None,
        "dca": None,
        "indicators": None,
        "portfolio_nav": None,
        "backtest": None,
        "backtest_curve": None,
    }

    # ---- 辅助：检查文件是否存在并加入结果 ----
    def _add_result(key, rel_path):
        full = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path)
        if os.path.exists(full):
            result_paths[key] = rel_path
        else:
            result_paths[key] = None

    try:
        # ============================================================
        # 步骤 1: 创建输出目录
        # ============================================================
        _log("正在创建输出目录...")
        _progress(5)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)

        # ============================================================
        # 步骤 2: 读取基金数据 & 资产配置
        # ============================================================
        _log("正在读取持仓数据...")
        _progress(10)

        _log("正在使用最新净值重估持仓市值与收益...")
        try:
            # 先统一估值，确保后续总览、CSV 和图表使用同一持仓口径。
            valued_data, valuation_results = update_holdings_market_values(refresh=True)
            portfolio = portfolio_tuples(valued_data)
            updated_count = sum(row["status"] == "updated" for row in valuation_results)
            error_rows = [row for row in valuation_results if row["status"] == "error"]
            _log(f"已完成 {updated_count} 只持仓基金的净值重估")
            for row in error_rows:
                _log(f"[警告] {row['name']} 重估失败：{row['error']}")
        except Exception as e:
            _log(f"[错误] 持仓净值重估失败，将保留上次估值：{e}")
            errors.append(f"持仓净值重估：{e}")

        _log("正在进行资产配置分析...")
        _ = analyze_asset_allocation(portfolio)  # 生成 asset_allocation.png
        _add_result("asset_allocation", "output/asset_allocation.png")
        _progress(20)

        # ============================================================
        # 步骤 3: 收益分析
        # ============================================================
        _log("正在计算组合收益...")
        # 收益摘要使用刚完成估值的持仓数据。
        summary = analyze_returns(portfolio)
        result_data["summary"] = summary
        _progress(30)

        # 保存 summary.csv 和 holdings_result.csv
        try:
            save_summary_csv(summary)
            _add_result("summary", "output/summary.csv")
        except Exception as e:
            _log(f"[警告] 保存 summary.csv 失败: {e}")

        try:
            holdings_path = os.path.join(OUTPUT_DIR, "holdings_result.csv")
            pd.DataFrame(position_rows(load_data())).to_csv(
                holdings_path, index=False, encoding="utf-8-sig"
            )
            _add_result("holdings_result", "output/holdings_result.csv")
        except Exception as e:
            _log(f"[警告] 保存 holdings_result.csv 失败: {e}")

        # ============================================================
        # 步骤 4: 净值数据分析（xalpha 网络请求）
        # ============================================================
        _log("正在获取基金净值数据（需要联网，可能需要一些时间）...")
        _progress(40)
        df_returns = None
        try:
            # 数据源失败时由缓存层回退已有真实净值，不生成随机结果。
            df_returns = analyze_netvalue(portfolio)
            result_data["period_returns"] = df_returns
            # 保存 period_return.csv
            try:
                save_period_return_csv(df_returns, portfolio)
                _add_result("period_return", "output/period_return.csv")
            except Exception as e:
                _log(f"[警告] 保存 period_return.csv 失败: {e}")
        except Exception as e:
            _log(f"[错误] 净值数据分析失败: {e}")
            _log("将跳过净值相关分析，继续后续步骤...")
            errors.append(f"净值数据分析：{e}")
        _progress(55)

        # ============================================================
        # 步骤 5: 风险分析
        # ============================================================
        _log("正在进行风险分析...")
        df_risk = None
        try:
            df_risk = analyze_risk(portfolio)
            result_data["risk"] = df_risk
            _add_result("risk_report", "output/risk_report.csv")
        except Exception as e:
            _log(f"[错误] 风险分析失败: {e}")
            errors.append(f"风险分析：{e}")
        _progress(70)

        # ============================================================
        # 步骤 6: 图表生成
        # ============================================================
        _log("正在生成图表...")
        if df_returns is not None:
            try:
                plot_category_return(df_returns, portfolio)
                _add_result("category_return", "output/category_return.png")
            except Exception as e:
                _log(f"[警告] 生成 category_return.png 失败: {e}")
        else:
            _log("[跳过] category_return.png: 无净值数据")

        if df_risk is not None:
            try:
                plot_risk_comparison(df_risk)
                _add_result("risk_comparison", "output/risk_comparison.png")
            except Exception as e:
                _log(f"[警告] 生成 risk_comparison.png 失败: {e}")
        else:
            _log("[跳过] risk_comparison.png: 无风险数据")
        _progress(80)

        # ============================================================
        # 步骤 7: 定投模拟
        # ============================================================
        _log("正在进行定投模拟...")
        invest_results = {}
        try:
            invest_results = run_fixed_investment_demo()
            result_data["dca"] = invest_results
            if invest_results:
                try:
                    save_dca_csv(invest_results)
                    _add_result("dca_result", "output/dca_result.csv")
                except Exception as e:
                    _log(f"[警告] 保存 dca_result.csv 失败: {e}")
        except Exception as e:
            _log(f"[错误] 定投模拟失败: {e}")
            errors.append(f"定投模拟：{e}")
        _progress(86)

        _log("正在计算通用量化指标和组合净值...")
        try:
            holdings = load_data().get("holdings", [])
            indicator_df, portfolio_nav = build_indicator_dataset(holdings)
            result_data["indicators"] = indicator_df
            result_data["portfolio_nav"] = portfolio_nav
            _add_result("indicator_dataset", "output/indicator_dataset.csv")
            _add_result("portfolio_nav", "output/portfolio_nav.csv")
        except Exception as e:
            _log(f"[错误] 通用量化指标计算失败: {e}")
            errors.append(f"量化指标：{e}")
        _progress(92)

        _log("正在运行默认策略回测...")
        try:
            funds = list_funds(enabled_only=True)
            if funds:
                backtest_df, backtest_curve = run_backtests(funds[0]["code"])
                result_data["backtest"] = backtest_df
                result_data["backtest_curve"] = backtest_curve
                _add_result("backtest_result", "output/backtest_result.csv")
                _add_result("backtest_curve", "output/backtest_curve.csv")
        except Exception as e:
            _log(f"[错误] 默认策略回测失败: {e}")
            errors.append(f"默认策略回测：{e}")
        _progress(96)

        # ============================================================
        # 步骤 8: 生成 Markdown 报告
        # ============================================================
        _log("正在生成 Markdown 报告...")
        try:
            generate_markdown_report(
                summary=summary,
                df_returns=df_returns if df_returns is not None else pd.DataFrame(),
                df_risk=df_risk if df_risk is not None else pd.DataFrame(),
                invest_results=invest_results,
            )
            _add_result("report", "portfolio_report.md")
        except Exception as e:
            _log(f"[警告] 生成 Markdown 报告失败: {e}")

        # ============================================================
        # 完成
        # ============================================================
        _progress(100)
        missing = [
            key for key, value in result_paths.items()
            if key != "_data" and value is None
        ]
        if errors or missing:
            _log(
                f"[部分完成] 分析流程已结束；失败步骤 {len(errors)} 项，"
                f"未生成输出 {len(missing)} 项。请查看前述日志。"
            )
            result_paths["_status"] = "partial"
        else:
            _log("[完成] 分析完成，全部预期输出已保存。")
            result_paths["_status"] = "success"
        result_paths["_errors"] = errors
        result_paths["_missing_outputs"] = missing
        result_paths["_data"] = result_data

    except Exception as e:
        _log(f"[错误] 分析过程中发生致命错误: {e}")
        _log(traceback.format_exc())
        result_paths["_status"] = "failed"
        result_paths["_errors"] = errors + [str(e)]

    return result_paths


# 修复引用：generate_markdown_report 中需要 pd
import pandas as pd


def _main_cli():
    """命令行模式的入口函数"""
    print(APP_FULL_NAME)
    print("=" * 50)
    print("分析内容: 资产配置、收益、净值、风险、定投、指标、回测和报告")

    def cli_log(msg):
        print(f"  {msg}")

    def cli_progress(pct):
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"  [{bar}] {pct}%", end="\r" if pct < 100 else "\n")

    try:
        result_paths = run_full_analysis(
            progress_callback=cli_progress,
            log_callback=cli_log,
        )

        status = result_paths.get("_status", "failed")
        status_text = {
            "success": "分析完成",
            "partial": "分析部分完成",
            "failed": "分析失败",
        }.get(status, status)
        print(f"\n{status_text}，输出目录: {OUTPUT_DIR}")
        for key, path in result_paths.items():
            if key.startswith("_"):
                continue
            status = "OK" if path else "FAILED"
            print(f"    {status} {key}: {path or '未生成'}")

    except Exception as e:
        print(f"\n运行出错: {e}")
        traceback.print_exc()
        sys.exit(1)


# 命令行入口复用与 GUI 相同的分析流程。
if __name__ == "__main__":
    _main_cli()
