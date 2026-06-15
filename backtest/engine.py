"""Fixed DCA, dynamic DCA and moving-average backtests."""

import numpy as np
import pandas as pd

from indicators.fund_indicators import normalize_nav, performance_metrics


# 通用回测引擎只基于历史净值模拟，不包含真实费率、限购和到账延迟。
class BacktestEngine:
    def __init__(self, price, start_date=None, end_date=None):
        data = normalize_nav(price)
        if start_date:
            data = data[data["date"] >= pd.Timestamp(start_date)]
        if end_date:
            data = data[data["date"] <= pd.Timestamp(end_date)]
        if len(data) < 2:
            raise ValueError("所选时间区间净值数据不足")
        self.data = data.reset_index(drop=True)

    @staticmethod
    def _schedule(data, frequency):
        # 周频和月频取各周期第一条可用净值作为计划交易日。
        if frequency == "daily":
            return data.index
        period = "W" if frequency == "weekly" else "M"
        return data.groupby(data["date"].dt.to_period(period)).head(1).index

    def _finish(self, strategy, values, invested, trades):
        # 同时保留策略资产和累计投入，便于绘制阶梯型定投资金曲线。
        curve = self.data[["date"]].copy()
        curve["equity"] = values
        curve["invested"] = invested
        # 用“资产/投入”构造绩效净值，避免新增投入被误识别为投资收益。
        metric_nav = (
            curve.set_index("date")["equity"]
            / curve.set_index("date")["invested"].replace(0, np.nan)
        ).dropna()
        metrics = performance_metrics(metric_nav)
        metrics.update({
            "strategy": strategy, "final_value": float(curve["equity"].iloc[-1]),
            "total_invested": float(curve["invested"].iloc[-1]),
            "profit": float(curve["equity"].iloc[-1] - curve["invested"].iloc[-1]),
            "trade_count": trades,
        })
        if metrics["total_invested"] > 0:
            metrics["cash_return"] = metrics["profit"] / metrics["total_invested"]
        return {"metrics": metrics, "curve": curve}

    def fixed_dca(self, amount=100, frequency="monthly"):
        # 固定定投在每个计划日投入相同金额并按当日净值换算份额。
        schedule = set(self._schedule(self.data, frequency))
        units = cash = 0.0
        values, invested = [], []
        trades = 0
        for index, row in self.data.iterrows():
            if index in schedule:
                units += amount / row["nav"]
                cash += amount
                trades += 1
            values.append(units * row["nav"])
            invested.append(cash)
        return self._finish("固定定投", values, invested, trades)

    def dynamic_dca(self, amount=100, frequency="monthly", ma_window=60,
                    low_multiplier=1.5, high_multiplier=0.5):
        # 净值低于均线时提高投入，高于均线时降低投入。
        schedule = set(self._schedule(self.data, frequency))
        ma = self.data["nav"].rolling(ma_window, min_periods=1).mean()
        units = cash = 0.0
        values, invested = [], []
        trades = 0
        for index, row in self.data.iterrows():
            if index in schedule:
                multiplier = low_multiplier if row["nav"] < ma.iloc[index] else high_multiplier
                contribution = amount * multiplier
                units += contribution / row["nav"]
                cash += contribution
                trades += 1
            values.append(units * row["nav"])
            invested.append(cash)
        return self._finish("动态定投", values, invested, trades)

    def moving_average(self, initial_cash=10000, short_window=20, long_window=60):
        # 短均线上穿长均线时全额持有，反向时回到现金。
        if short_window >= long_window:
            raise ValueError("短期均线必须小于长期均线")
        short = self.data["nav"].rolling(short_window).mean()
        long = self.data["nav"].rolling(long_window).mean()
        cash, units = float(initial_cash), 0.0
        values, invested = [], []
        trades = 0
        for index, row in self.data.iterrows():
            signal = short.iloc[index] > long.iloc[index]
            if signal and units == 0 and not np.isnan(long.iloc[index]):
                units, cash, trades = cash / row["nav"], 0.0, trades + 1
            elif not signal and units > 0:
                cash, units, trades = units * row["nav"], 0.0, trades + 1
            values.append(cash + units * row["nav"])
            invested.append(initial_cash)
        return self._finish("均线策略", values, invested, trades)
