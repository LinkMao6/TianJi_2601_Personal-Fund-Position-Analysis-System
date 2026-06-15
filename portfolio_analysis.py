# -*- coding: utf-8 -*-
"""
xalpha 基金组合分析核心模块
============================
功能：资产配置 / 收益分析 / 净值分析 / 风险分析 / 定投模拟 / 可视化
依赖：xalpha, pandas, matplotlib, numpy
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 非交互式后端，不弹窗
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from app_logging import get_logger
from app_info import APP_FULL_NAME, DISCLAIMER
from data_provider import DataProviderError, build_default_provider
from fund_registry import fund_codes, get_fund

warnings.filterwarnings("ignore")
logger = get_logger(__name__)
DATA_PROVIDER = build_default_provider()

# ============================================================
# 0. 全局设置
# ============================================================

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# xalpha 数据缓存目录
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_xalpha")
os.makedirs(DATA_DIR, exist_ok=True)

# ------------------------------------------------------------
# 基金代码映射表（真实代码，从天天基金网可查）
# ------------------------------------------------------------
# Backward-compatible snapshot. New code should query fund_registry directly.
FUND_CODES = fund_codes()


def _fund_code(name):
    fund = get_fund(name=name)
    return fund["code"] if fund else FUND_CODES.get(name)

# ------------------------------------------------------------
# V1.0 随仓库提供的模拟持仓快照，仅供独立演示函数使用。
# GUI 和完整分析以 portfolio_data.json 中的持仓、交易和批次为准。
# ------------------------------------------------------------
PORTFOLIO_DATA = [
    # (基金名称, 类别, 持仓金额/元, 持有收益/元)
    ("富国中证A500ETF联接A", "A股宽基", 99.52, -0.60),
    ("南方深证成份ETF联接A", "A股宽基", 99.26, -0.74),
    ("易方达黄金ETF联接A", "黄金", 9.18, -0.82),
    ("长城短债债券A", "短债", 62.94, -0.06),
    ("华夏上证科创板综合ETF联接A", "科技成长", 100.82, 0.82),
    ("嘉实短债债券A", "短债", 79.95, -0.05),
    ("华安纳斯达克100ETF联接(QDII)A", "美股纳斯达克", 68.73, -1.27),
]


# ============================================================
# 1. 资产配置分析
# ============================================================

# 资产配置只汇总当前持仓市值，不使用基金净值历史。
def analyze_asset_allocation(portfolio=None):
    """
    统计各类别占比，保存饼图到 output/asset_allocation.png
    参数:
        portfolio: list of (name, category, amount, profit), 默认用 PORTFOLIO_DATA
    返回:
        allocation: dict {category: total_amount}
        总资产: float
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA

    # 汇总各类别金额
    alloc = {}
    for _, cat, amt, _ in portfolio:
        alloc[cat] = alloc.get(cat, 0) + amt

    total = sum(alloc.values())

    # ---- 饼图 ----
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = ["#2B579A", "#E74C3C", "#27AE60", "#F39C12", "#8E44AD", "#1ABC9C"]
    labels = list(alloc.keys())
    sizes = list(alloc.values())
    explode = [0.02] * len(labels)

    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        colors=colors[: len(labels)],
        explode=explode,
        pctdistance=0.75,
    )
    for t in autotexts:
        t.set_fontsize(10)
    ax.set_title("资产配置分布", fontsize=14, fontweight="bold")

    fig.savefig(os.path.join(OUTPUT_DIR, "asset_allocation.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"[资产配置] 总资产: CNY {total:,.2f}")
    for cat, amt in alloc.items():
        print(f"  {cat}: CNY {amt:,.2f}  ({amt/total*100:.1f}%)")

    return alloc, total


# ============================================================
# 2. 基金收益分析
# ============================================================

# 组合收益来自各持仓统一估值后的市值和收益。
def analyze_returns(portfolio=None):
    """
    统计组合收益情况
    参数:
        portfolio: list of (name, category, amount, profit)
    返回:
        summary: dict
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA

    total_assets = sum(p[2] for p in portfolio)          # 总资产 = 持仓金额合计
    total_profit = sum(p[3] for p in portfolio)           # 总收益
    total_cost = total_assets - total_profit              # 总成本
    total_return_rate = total_profit / total_cost * 100 if total_cost != 0 else 0

    profit_funds = [p for p in portfolio if p[3] > 0]
    loss_funds = [p for p in portfolio if p[3] < 0]

    print("=" * 50)
    print("         基金组合收益分析")
    print("=" * 50)
    print(f"  组合总资产:     CNY {total_assets:>12,.2f}")
    print(f"  组合总成本:     CNY {total_cost:>12,.2f}")
    print(f"  组合总收益:     CNY {total_profit:>12,.2f}")
    print(f"  组合收益率:     {total_return_rate:>11.2f}%")
    print(f"  盈利基金数量:   {len(profit_funds):>11}")
    print(f"  亏损基金数量:   {len(loss_funds):>11}")
    print("-" * 50)
    for name, cat, amt, profit in portfolio:
        rate = profit / (amt - profit) * 100 if (amt - profit) != 0 else 0
        tag = "+" if profit >= 0 else ""
        print(f"  {name:<20s} [{cat}]  金额:{amt:>8,.0f}  收益:{tag}{profit:>8,.0f}  ({rate:+.1f}%)")
    print("=" * 50)

    return {
        "total_assets": total_assets,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "total_return_rate": total_return_rate,
        "profit_count": len(profit_funds),
        "loss_count": len(loss_funds),
    }


# ============================================================
# 3. xalpha 净值数据分析
# ============================================================

# 封装数据源读取并保留来源状态，失败时返回 None 而不是随机结果。
def _get_fund_info(code, name):
    """
    安全获取 fundinfo / mfundinfo 对象，失败返回 None
    自动识别货币基金并使用正确的类
    """
    try:
        result = DATA_PROVIDER.get_fund_nav(code, refresh=False)
        if result.stale:
            print(f"  [缓存] {name}({code}) 使用本地缓存，更新时间 {result.updated_at}")
        return result
    except DataProviderError as e:
        logger.error("获取 %s(%s) 失败: %s", name, code, e)
        print(f"  [错误] 获取 {name}({code}) 失败: {e}")
        return None


# 月度阶段收益按日历区间回溯，而不是用固定天数近似月份。
CALENDAR_RETURN_PERIODS = {
    30: pd.DateOffset(months=1),
    90: pd.DateOffset(months=3),
    180: pd.DateOffset(months=6),
    365: pd.DateOffset(years=1),
}


# 收益风险优先使用累计净值；详情页补入的最新正式单位净值若尚无累计净值，
# 不参与累计收益指标，避免一条不完整记录使整只基金结果变成 NaN。
def _metric_price(fi):
    df = fi.price.copy()
    df["date"] = pd.to_datetime(df["date"])
    if "totvalue" in df.columns:
        df["metric_value"] = pd.to_numeric(df["totvalue"], errors="coerce")
        complete = df.dropna(subset=["date", "metric_value"])
        if not complete.empty:
            return complete.sort_values("date")
    df["metric_value"] = pd.to_numeric(df["netvalue"], errors="coerce")
    return df.dropna(subset=["date", "metric_value"]).sort_values("date")


# 目标日期没有净值时取该日期之前最近交易日。
def _period_return_from_price(fi, days):
    """
    从 price DataFrame 计算近 N 个自然日的收益率
    用最近交易日净值 vs N天前最近交易日净值
    """
    df = _metric_price(fi)
    if df.empty:
        return None

    end_date = df["date"].max()
    start_date = end_date - CALENDAR_RETURN_PERIODS.get(
        days, pd.Timedelta(days=days)
    )

    # 找最接近 start_date 的交易日
    before = df[df["date"] <= start_date]
    if before.empty:
        return None
    start_row = before.iloc[-1]
    end_row = df[df["date"] == end_date].iloc[0]

    return (end_row["metric_value"] / start_row["metric_value"] - 1) * 100


# 对持仓基金逐一计算阶段收益，并记录净值日期和数据来源。
def analyze_netvalue(portfolio=None):
    """
    利用 xalpha 读取基金历史净值，计算各周期收益率
    返回:
        DataFrame: 各基金各周期收益率
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA

    periods = {"近30日": 30, "近90日": 90, "近180日": 180, "近1年": 365}
    results = []

    # 去重基金（同一代码只取一次）
    seen_codes = set()

    for name, cat, amt, profit in portfolio:
        code = _fund_code(name)
        if code is None or code in seen_codes:
            continue
        seen_codes.add(code)

        print(f"  读取 {name} ({code}) ...")
        fi = _get_fund_info(code, name)
        if fi is None:
            row = {"基金名称": name, "基金代码": code}
            for label in periods:
                row[label] = "N/A"
            row["数据来源"] = "不可用"
            results.append(row)
            continue

        row = {"基金名称": name, "基金代码": code, "数据来源": fi.source}
        metric_price = _metric_price(fi)
        row["净值截止日期"] = (
            metric_price["date"].max().date().isoformat()
            if not metric_price.empty else ""
        )
        for label, d in periods.items():
            r = _period_return_from_price(fi, d)
            if r is not None:
                row[label] = round(r, 4)
            else:
                row[label] = "N/A"
        results.append(row)

    df_returns = pd.DataFrame(results)
    print("\n[净值收益率]")
    print(df_returns.to_string(index=False))
    return df_returns


# ============================================================
# 4. 风险分析
# ============================================================

# 年化收益按最近一年实际日期跨度复合计算。
def _annualized_return_from_price(fi):
    """从净值序列计算年化收益率"""
    df = _metric_price(fi)
    if len(df) < 2:
        return None

    # 取最近一年
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=365)
    recent = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
    if len(recent) < 2:
        # 用全部数据
        recent = df

    total_ret = recent["metric_value"].iloc[-1] / recent["metric_value"].iloc[0] - 1
    years = (recent["date"].iloc[-1] - recent["date"].iloc[0]).days / 365.25
    if years == 0:
        return None
    annual_ret = (1 + total_ret) ** (1 / years) - 1
    return annual_ret


# 日收益率标准差按 252 个交易日换算成年化波动率。
def _annualized_volatility_from_price(fi):
    """从净值序列计算年化波动率"""
    df = _metric_price(fi)
    if len(df) < 2:
        return None

    # 取最近一年
    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=365)
    recent = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
    if len(recent) < 2:
        recent = df

    # 日收益率
    returns = recent["metric_value"].pct_change().dropna()
    if len(returns) < 2:
        return None
    daily_vol = returns.std()
    annual_vol = daily_vol * np.sqrt(252)
    return annual_vol


# 最大回撤衡量历史峰值到后续低点的最大跌幅。
def _max_drawdown_from_price(fi):
    """从净值序列计算最大回撤"""
    df = _metric_price(fi)
    if df.empty:
        return None

    values = df["metric_value"].values
    peak = values[0]
    max_dd = 0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


# 夏普比率使用固定 2% 年化无风险利率作为比较基准。
def _sharpe_from_price(fi, risk_free=0.02):
    """计算夏普比率 (无风险利率默认2%)"""
    ann_ret = _annualized_return_from_price(fi)
    ann_vol = _annualized_volatility_from_price(fi)
    if ann_ret is None or ann_vol is None or ann_vol == 0:
        return None
    return (ann_ret - risk_free) / ann_vol


# 风险分析沿用同一真实净值数据，不在失败时伪造指标。
def analyze_risk(portfolio=None):
    """
    风险分析：年化收益率、年化波动率、最大回撤、夏普比率
    保存 risk_report.csv 到 output/
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA

    results = []
    seen_codes = set()

    for name, cat, amt, profit in portfolio:
        code = _fund_code(name)
        if code is None or code in seen_codes:
            continue
        seen_codes.add(code)

        print(f"  风险分析 {name} ({code}) ...")
        fi = _get_fund_info(code, name)

        if fi is None:
            results.append({
                "基金名称": name,
                "基金代码": code,
                "年化收益率": "N/A",
                "年化波动率": "N/A",
                "最大回撤": "N/A",
                "夏普比率": "N/A",
                "数据来源": "不可用",
            })
            continue

        ann_ret = _annualized_return_from_price(fi)
        ann_vol = _annualized_volatility_from_price(fi)
        max_dd = _max_drawdown_from_price(fi)
        sharpe = _sharpe_from_price(fi)

        results.append({
            "基金名称": name,
            "基金代码": code,
            "净值截止日期": _metric_price(fi)["date"].max().date().isoformat(),
            "年化收益率": f"{ann_ret*100:.2f}%" if ann_ret is not None else "N/A",
            "年化波动率": f"{ann_vol*100:.2f}%" if ann_vol is not None else "N/A",
            "最大回撤": f"{max_dd*100:.2f}%" if max_dd is not None else "N/A",
            "夏普比率": round(sharpe, 2) if sharpe is not None else "N/A",
            "数据来源": fi.source,
        })

    df_risk = pd.DataFrame(results)
    csv_path = os.path.join(OUTPUT_DIR, "risk_report.csv")
    df_risk.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n[风险报告] 已保存至 {csv_path}")
    print(df_risk.to_string(index=False))
    return df_risk


# ============================================================
# 5. 可视化
# ============================================================

def plot_category_return(df_returns, portfolio=None):
    """
    各类别平均收益率柱状图 → output/category_return.png
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA

    # 建立基金名→类别映射
    name2cat = {}
    for name, cat, _, _ in portfolio:
        name2cat[name] = cat

    # 只取"近30日"列
    if "近30日" not in df_returns.columns:
        print("[跳过] category_return.png: 无近30日数据")
        return

    df = df_returns[["基金名称", "近30日"]].copy()
    df["类别"] = df["基金名称"].map(name2cat)
    # 过滤掉非数值
    df = df[pd.to_numeric(df["近30日"], errors="coerce").notna()]
    df["近30日"] = df["近30日"].astype(float)

    cat_avg = df.groupby("类别")["近30日"].mean().sort_values()

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ["#2B579A", "#E74C3C", "#27AE60", "#F39C12", "#8E44AD", "#1ABC9C"]
    return_colors = ["#E74C3C" if value >= 0 else "#27AE60" for value in cat_avg.values]
    bars = ax.barh(cat_avg.index, cat_avg.values, color=return_colors)
    ax.set_xlabel("近30日平均收益率 (%)", fontsize=11)
    ax.set_title("各类别近30日平均收益率", fontsize=14, fontweight="bold")
    for bar, val in zip(bars, cat_avg.values):
        ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2,
                f"{val:.2f}%", va="center", fontsize=10)
    ax.axvline(0, color="gray", linewidth=0.8)

    fig.savefig(os.path.join(OUTPUT_DIR, "category_return.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[图表] category_return.png 已保存")


def plot_risk_comparison(df_risk):
    """
    风险指标对比图 → output/risk_comparison.png
    """
    # 只保留真实或缓存数据
    df = df_risk[df_risk["数据来源"].isin(["xalpha", "本地缓存", "真实"])].copy()
    if df.empty:
        print("[跳过] risk_comparison.png: 无真实风险数据")
        return

    # 解析百分比字符串
    for col in ["年化收益率", "年化波动率", "最大回撤"]:
        df[col] = df[col].str.replace("%", "").astype(float)

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # --- 年化收益率 ---
    return_colors = ["#E74C3C" if value >= 0 else "#27AE60" for value in df["年化收益率"]]
    axes[0].barh(df["基金名称"], df["年化收益率"], color=return_colors)
    axes[0].set_title("年化收益率 (%)", fontsize=12, fontweight="bold")
    axes[0].axvline(0, color="gray", linewidth=0.8)

    # --- 年化波动率 ---
    axes[1].barh(df["基金名称"], df["年化波动率"], color="#E74C3C")
    axes[1].set_title("年化波动率 (%)", fontsize=12, fontweight="bold")

    # --- 最大回撤 ---
    axes[2].barh(df["基金名称"], df["最大回撤"], color="#F39C12")
    axes[2].set_title("最大回撤 (%)", fontsize=12, fontweight="bold")

    for ax in axes:
        ax.tick_params(axis="y", labelsize=9)

    fig.tight_layout()
    fig.savefig(os.path.join(OUTPUT_DIR, "risk_comparison.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[图表] risk_comparison.png 已保存")


# ============================================================
# 6. 定投模拟
# ============================================================

# 固定投入模拟按可用净值日期买入，不包含真实费用和到账延迟。
def simulate_fixed_investment(fund_name, daily_amount, periods):
    """
    定投模拟（基于真实净值数据）
    参数:
        fund_name: 基金名称（FUND_CODES 中的 key）
        daily_amount: 每日定投金额（元）
        periods: list of int, 模拟天数列表，如 [180, 365, 730]
    返回:
        dict: {days: {累计投入, 累计份额, 市值, 成本, 收益率}}
    """
    code = _fund_code(fund_name)
    if code is None:
        print(f"  [错误] 未找到基金代码: {fund_name}")
        return {}

    print(f"\n  定投模拟: {fund_name} ({code}), 每日 CNY {daily_amount}")
    fi = _get_fund_info(code, fund_name)
    if fi is None:
        print(f"  [错误] 无法获取 {fund_name} 数据，定投结果不可用")
        return {}

    df = fi.price.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    results = {}
    for days in sorted(periods, reverse=True):
        end_date = df["date"].max()
        start_date = end_date - pd.Timedelta(days=days)
        period_df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

        if period_df.empty:
            continue

        total_shares = 0
        total_invested = 0
        trade_days = 0

        for _, row in period_df.iterrows():
            # 每个交易日投入 daily_amount
            total_shares += daily_amount / row["netvalue"]
            total_invested += daily_amount
            trade_days += 1

        final_value = total_shares * period_df["netvalue"].iloc[-1]
        avg_cost = total_invested / total_shares if total_shares > 0 else 0
        return_rate = (final_value - total_invested) / total_invested * 100

        results[days] = {
            "累计投入": round(total_invested, 2),
            "累计份额": round(total_shares, 2),
            "市值": round(final_value, 2),
            "成本": round(avg_cost, 4),
            "收益率": f"{return_rate:.2f}%",
            "交易天数": trade_days,
        }

        print(f"    {days}天: 投入CNY {total_invested:,.0f} → 市值CNY {final_value:,.0f} (收益率{return_rate:.2f}%)")

    return results


def run_fixed_investment_demo():
    """
    运行定投演示:
      华安纳斯达克100A  每日10元
      长城短债A         每日10元
    模拟周期: 半年(180天), 一年(365天), 两年(730天)
    """
    print("\n" + "=" * 60)
    print("         定 投 模 拟")
    print("=" * 60)

    periods = [180, 365, 730]
    period_labels = {180: "半年", 365: "一年", 730: "两年"}

    all_results = {}

    for fund_name in ["华安纳斯达克100ETF联接(QDII)A", "长城短债债券A"]:
        res = simulate_fixed_investment(fund_name, daily_amount=10, periods=periods)
        all_results[fund_name] = res

    # 汇总打印
    print("\n" + "-" * 60)
    print(f"{'周期':<8} {'基金':<30s} {'投入':>10s} {'市值':>10s} {'收益率':>8s}")
    print("-" * 60)
    for fund_name, res in all_results.items():
        for days in sorted(res.keys()):
            d = res[days]
            label = period_labels.get(days, f"{days}天")
            source = d.get("数据来源", "真实")
            print(f"{label:<8} {fund_name:<30s} CNY {d['累计投入']:>8,.0f}  CNY {d['市值']:>8,.0f}  {d['收益率']:>8s}  [{source}]")
    print("-" * 60)

    return all_results


# ============================================================
# 7. 综合运行
# ============================================================

def run_all():
    """
    一键运行全部分析
    """
    print("\n" + "=" * 60)
    print("   xalpha 基金组合分析系统")
    print("=" * 60)

    # 1. 资产配置
    print("\n## 1. 资产配置分析")
    alloc, total = analyze_asset_allocation()

    # 2. 收益分析
    print("\n## 2. 基金收益分析")
    summary = analyze_returns()

    # 3. 净值分析
    print("\n## 3. xalpha 净值数据分析")
    df_returns = analyze_netvalue()

    # 4. 风险分析
    print("\n## 4. 风险分析")
    df_risk = analyze_risk()

    # 5. 可视化
    print("\n## 5. 生成图表")
    plot_category_return(df_returns)
    plot_risk_comparison(df_risk)

    # 6. 定投模拟
    print("\n## 6. 定投模拟")
    invest_results = run_fixed_investment_demo()

    print("\n" + "=" * 60)
    print("   分析完成！所有图表已保存至 output/ 文件夹")
    print("=" * 60)

    return {
        "allocation": alloc,
        "total": total,
        "summary": summary,
        "df_returns": df_returns,
        "df_risk": df_risk,
        "invest_results": invest_results,
    }


# ============================================================
# 8. 输出文件生成（供 UI 使用）
# ============================================================

# CSV 使用 utf-8-sig，便于 Windows Excel 正确识别中文。
def save_summary_csv(summary, path=None):
    """
    保存组合总览 CSV → output/summary.csv
    参数:
        summary: analyze_returns() 返回的 dict
        path: 输出路径，默认 output/summary.csv
    """
    import datetime
    if path is None:
        path = os.path.join(OUTPUT_DIR, "summary.csv")

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = [
        ("组合总资产", f"CNY {summary['total_assets']:,.2f}"),
        ("组合总成本", f"CNY {summary['total_cost']:,.2f}"),
        ("组合总收益", f"CNY {summary['total_profit']:,.2f}"),
        ("组合收益率", f"{summary['total_return_rate']:.2f}%"),
        ("盈利基金数量", str(summary["profit_count"])),
        ("亏损基金数量", str(summary["loss_count"])),
        ("更新时间", now_str),
    ]
    df = pd.DataFrame(rows, columns=["metric", "value"])
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] summary.csv 已保存至 {path}")
    return path


def save_holdings_csv(portfolio=None, path=None):
    """
    保存持仓明细 CSV → output/holdings_result.csv
    参数:
        portfolio: list of (name, category, amount, profit)
        path: 输出路径
    """
    if portfolio is None:
        portfolio = PORTFOLIO_DATA
    if path is None:
        path = os.path.join(OUTPUT_DIR, "holdings_result.csv")

    total_assets = sum(p[2] for p in portfolio)
    rows = []
    for name, cat, amt, profit in portfolio:
        cost = amt - profit
        rate = (profit / cost * 100) if cost != 0 else 0
        ratio = (amt / total_assets * 100) if total_assets != 0 else 0
        code = _fund_code(name) or ""
        rows.append({
            "基金名称": name,
            "基金代码": code,
            "资产类别": cat,
            "持仓金额": round(amt, 2),
            "持有收益": round(profit, 2),
            "持有收益率": f"{rate:.2f}%",
            "持仓占比": f"{ratio:.1f}%",
        })

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] holdings_result.csv 已保存至 {path}")
    return path


def save_period_return_csv(df_returns, portfolio=None, path=None):
    """
    保存各周期收益率 CSV → output/period_return.csv
    参数:
        df_returns: analyze_netvalue() 返回的 DataFrame
        portfolio: 持仓列表（用于添加类别列）
        path: 输出路径
    """
    if path is None:
        path = os.path.join(OUTPUT_DIR, "period_return.csv")

    df = df_returns.copy()

    # 添加资产类别列
    if portfolio is None:
        portfolio = PORTFOLIO_DATA
    name2cat = {p[0]: p[1] for p in portfolio}
    df["资产类别"] = df["基金名称"].map(name2cat)

    # 重新排列列顺序
    cols = ["基金名称", "基金代码", "资产类别"]
    for c in df.columns:
        if c not in cols:
            cols.append(c)
    df = df[cols]

    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] period_return.csv 已保存至 {path}")
    return path


def save_dca_csv(invest_results, path=None):
    """
    保存定投模拟结果 CSV → output/dca_result.csv
    参数:
        invest_results: run_fixed_investment_demo() 返回的嵌套 dict
        path: 输出路径
    """
    if path is None:
        path = os.path.join(OUTPUT_DIR, "dca_result.csv")

    period_labels = {180: "半年(180天)", 365: "一年(365天)", 730: "两年(730天)"}
    rows = []
    for fund_name, periods in invest_results.items():
        for days, data in sorted(periods.items()):
            ret_str = data.get("收益率", "N/A")

            rows.append({
                "基金名称": fund_name,
                "定投频率": "每日",
                "单次金额": 10.00,
                "模拟周期": period_labels.get(days, f"{days}天"),
                "累计投入": data.get("累计投入", 0),
                "当前市值": data.get("市值", 0),
                "收益金额": round(data.get("市值", 0) - data.get("累计投入", 0), 2),
                "收益率": ret_str,
            })

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[输出] dca_result.csv 已保存至 {path}")
    return path


# Markdown 报告只汇总已有分析结果，不承担新的计算逻辑。
def generate_markdown_report(summary, df_returns, df_risk, invest_results, path=None):
    """
    生成完整的 Markdown 报告 → portfolio_report.md
    参数:
        summary: analyze_returns() 返回的 dict
        df_returns: analyze_netvalue() 返回的 DataFrame
        df_risk: analyze_risk() 返回的 DataFrame
        invest_results: run_fixed_investment_demo() 返回的嵌套 dict
        path: 输出路径，默认项目根目录 portfolio_report.md
    """
    import datetime
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_report.md")

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append(f"# {APP_FULL_NAME} - 组合分析报告")
    lines.append("")
    lines.append(f"> 生成时间: {now_str}")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、资产配置")
    lines.append("")
    lines.append("![资产配置](output/asset_allocation.png)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 二、收益概况")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 组合总资产 | CNY {summary['total_assets']:,.2f} |")
    lines.append(f"| 组合总成本 | CNY {summary['total_cost']:,.2f} |")
    lines.append(f"| 组合总收益 | CNY {summary['total_profit']:,.2f} |")
    lines.append(f"| 组合收益率 | {summary['total_return_rate']:.2f}% |")
    lines.append(f"| 盈利基金数量 | {summary['profit_count']} |")
    lines.append(f"| 亏损基金数量 | {summary['loss_count']} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 三、净值收益率")
    lines.append("")
    # 构建 Markdown 表格
    if df_returns is not None and not df_returns.empty:
        cols = [c for c in df_returns.columns if c != "资产类别"]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["------"] * len(cols)) + "|")
        for _, row in df_returns.iterrows():
            vals = [str(row[c]) for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
    else:
        lines.append("_暂无数据_")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 四、风险指标")
    lines.append("")
    lines.append("详见 [risk_report.csv](output/risk_report.csv)")
    lines.append("")
    if df_risk is not None and not df_risk.empty:
        cols = df_risk.columns.tolist()
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["------"] * len(cols)) + "|")
        for _, row in df_risk.iterrows():
            vals = [str(row[c]) for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("![风险对比](output/risk_comparison.png)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 五、类别收益")
    lines.append("")
    lines.append("![类别收益](output/category_return.png)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 六、定投模拟")
    lines.append("")
    if invest_results:
        period_labels = {180: "半年(180天)", 365: "一年(365天)", 730: "两年(730天)"}
        lines.append("| 基金 | 周期 | 累计投入 | 当前市值 | 收益率 |")
        lines.append("|------|------|----------|----------|--------|")
        for fund_name, periods in invest_results.items():
            for days, data in sorted(periods.items()):
                label = period_labels.get(days, f"{days}天")
                lines.append(
                    f"| {fund_name} | {label} | CNY {data.get('累计投入', 0):,.0f} | "
                    f"CNY {data.get('市值', 0):,.0f} | {data.get('收益率', 'N/A')} |"
                )
    else:
        lines.append("_暂无数据_")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*报告由 {APP_FULL_NAME} 自动生成*")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[输出] portfolio_report.md 已保存至 {path}")
    return path


# ============================================================
# 供 Notebook 使用的教学函数
# ============================================================

def demo_read_fund(code="000307"):
    """
    演示: 如何读取一只基金
    返回 fundinfo 对象
    """
    print(f"读取基金 {code} ...")
    fi = DATA_PROVIDER.get_fund_nav(code)
    print(f"基金名称: {fi.name}")
    print(f"净值数据 (最近5条):")
    print(fi.price.tail())
    return fi


def demo_plot_netvalue(fi):
    """演示: 画净值走势图"""
    df = fi.price.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df["date"], df["totvalue"], linewidth=1, color="#2B579A")
    ax.set_title(f"{fi.name} 累计净值走势", fontsize=14)
    ax.set_xlabel("日期")
    ax.set_ylabel("累计净值")
    fig.tight_layout()
    fig.savefig(os.path.join(OUTPUT_DIR, "demo_netvalue.png"), dpi=150)
    plt.close(fig)
    print(f"[图表] demo_netvalue.png 已保存")


def demo_calculate_return(fi, days=90):
    """演示: 计算阶段收益率"""
    r = _period_return_from_price(fi, days)
    print(f"{fi.name} 近{days}日收益率: {r:.2f}%" if r is not None else "数据不足")
    return r


def demo_max_drawdown(fi):
    """演示: 计算最大回撤"""
    mdd = _max_drawdown_from_price(fi)
    print(f"{fi.name} 最大回撤: {mdd*100:.2f}%")
    return mdd


def demo_fixed_invest(fi, daily_amount=10, days=180):
    """演示: 简单定投模拟"""
    df = fi.price.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=days)
    period_df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

    total_shares = 0
    total_invested = 0
    for _, row in period_df.iterrows():
        total_shares += daily_amount / row["netvalue"]
        total_invested += daily_amount

    final_value = total_shares * period_df["netvalue"].iloc[-1]
    return_rate = (final_value - total_invested) / total_invested * 100

    print(f"定投 {fi.name}, 每日CNY {daily_amount}, {days}天")
    print(f"  累计投入: CNY {total_invested:,.2f}")
    print(f"  当前市值: CNY {final_value:,.2f}")
    print(f"  收益率:   {return_rate:.2f}%")

    return {
        "累计投入": total_invested,
        "当前市值": final_value,
        "收益率": return_rate,
    }


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    run_all()
