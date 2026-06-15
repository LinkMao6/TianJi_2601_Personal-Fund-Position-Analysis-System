# 模块依赖说明

```text
main.py
└── ui_app.py
    ├── app_info.py
    ├── fund_registry.py
    ├── data_provider.py
    ├── portfolio_store.py
    ├── quant_service.py
    ├── recommendation/
    └── run_portfolio.py
        └── portfolio_analysis.py
            ├── fund_registry.py
            └── data_provider.py

portfolio_store.py
└── fund_registry.py

fund_registry.py
└── fund_pool.json

data_provider.py
├── fund_registry.py
├── xalpha
└── data_xalpha/cache/

quant_service.py
├── indicators/
│   ├── fund_indicators.py
│   └── portfolio_indicators.py
└── backtest/
    └── engine.py

recommendation/
├── models.py
├── scoring.py
├── engine.py
└── service.py
    ├── data_provider.py
    ├── fund_registry.py
    ├── indicators/
    └── portfolio_store.py
```

## 职责边界

- `main.py`：V1.0 统一 GUI 启动入口。
- `app_info.py`：统一软件名称、简称、版本号和免责声明。
- `fund_registry.py`：基金池配置、校验、增删改和启停。
- `data_provider.py`：数据源接口、xalpha 适配、缓存和数据源状态。
- `portfolio_store.py`：持仓、交易、lot 和版本迁移。
- `portfolio_analysis.py`：收益、风险、定投、图表和报告计算。
- `indicators/`：单基金及组合级收益、风险和趋势指标。
- `backtest/`：固定定投、动态定投和均线策略的通用回测引擎。
- `quant_service.py`：指标、组合净值、回测 CSV 的应用层编排。
- `recommendation/models.py`：建议与数据质量模型。
- `recommendation/scoring.py`：趋势、位置、风险、持仓评分。
- `recommendation/engine.py`：普通建议、定投建议、卖出信号及基金类型规则。
- `recommendation/service.py`：批量生成、历史保存和报告导出。
- `run_portfolio.py`：完整分析流程编排。
- `ui_app.py`：Tkinter 展示和用户交互。

## 运行数据

- `fund_pool.json`：基金池。
- `portfolio_data.json`：版本化组合、交易和 lot 数据。
- `data_xalpha/cache/*.csv`：基金净值缓存。
- `data_xalpha/cache/provider_status.json`：数据源状态。
- `logs/xalpha_portfolio.log`：轮转运行日志。
- `output/indicator_dataset.csv`：基金与组合通用指标。
- `output/portfolio_nav.csv`：组合净值曲线。
- `output/backtest_result.csv`：策略回测绩效。
- `output/backtest_curve.csv`：策略资金曲线。
- `data/history/fund_recommendations.csv`：建议历史记录。
- `output/fund_recommendation_report.md`：建议导出报告。

V1.0 统一 GUI 入口为 `python main.py`，原 `python ui_app.py` 保留兼容；CLI 入口为 `python run_portfolio.py`。

## 第三方与原创边界

第三方能力包括 xalpha、pandas、numpy、matplotlib、Tkinter/ttk 及 Python 标准库。项目原创部分为基金池、数据源封装与缓存、持仓交易批次、统一估值、指标组织、回测、策略建议、GUI 交互、报告和日志等业务代码。第三方库源码不属于项目原创源码。
