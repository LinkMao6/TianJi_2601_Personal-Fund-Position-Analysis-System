# -*- coding: utf-8 -*-
"""Versioned portfolio, transaction and lot storage."""

import json
import os
import uuid
from datetime import date, datetime

import pandas as pd

from app_logging import get_logger
from data_provider import DataProviderError, build_default_provider
from fund_registry import get_fund
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_data.json")
logger = get_logger(__name__)


# 通过基金池补齐代码和分类，兼容早期只保存名称的持仓数据。
def _fund_values(name, code=""):
    fund = get_fund(code=code) if code else None
    fund = fund or get_fund(name=name) or {}
    return fund.get("code", ""), fund.get("category", "未分类")


# 当前仓库中的 portfolio_data.json 是明确标识的 V1.0 演示数据。
# 文件不存在时创建空结构，避免另一套内置金额被误当成正式持仓。
def _default_data():
    return {
        "version": 3,
        "data_profile": "user",
        "holdings": [],
        "transactions": [],
        "lots": [],
        "position_summary": {},
    }


# 数据迁移只补字段和建立旧持仓批次，不凭空生成历史交易。
def _migrate(data):
    data.setdefault("data_profile", "user")
    data.setdefault("holdings", [])
    data.setdefault("transactions", [])
    data.setdefault("lots", [])
    for holding in data["holdings"]:
        code, category = _fund_values(
            holding.get("name", ""), holding.get("code", "")
        )
        holding.setdefault("code", code)
        holding.setdefault("category", category)
        holding.setdefault("amount", 0.0)
        holding.setdefault("profit", 0.0)
        holding.setdefault("manual_amount", holding.get("amount", 0.0))
        holding.setdefault("manual_profit", holding.get("profit", 0.0))
        holding.setdefault("units", 0.0)
        holding.setdefault(
            "total_cost", round(float(holding["amount"]) - float(holding["profit"]), 2)
        )
        if (
            holding.get("valuation_unit_source") in {
                "inferred_from_previous_value", "mixed_estimated"
            }
            and float(holding.get("valuation_units", 0)) > 0
        ):
            holding.setdefault(
                "legacy_estimated_units", float(holding["valuation_units"])
            )
    for tx in data["transactions"]:
        tx.setdefault("confirmed_nav", tx.get("nav", 0.0))
        tx.setdefault("confirmed_units", tx.get("shares", 0.0))
        tx.setdefault("fee", 0.0)
    if not data["lots"]:
        # 旧持仓缺少买入日期和确认份额，因此来源标记为 legacy_migration。
        for holding in data["holdings"]:
            if float(holding.get("amount", 0)) <= 0:
                continue
            data["lots"].append({
                "lot_id": uuid.uuid4().hex,
                "fund_code": holding.get("code", ""),
                "fund_name": holding["name"],
                "buy_date": "",
                "buy_amount": float(holding.get("total_cost", 0)),
                "confirmed_nav": 0.0,
                "confirmed_units": float(holding.get("units", 0)),
                "remaining_units": float(holding.get("units", 0)),
                "fee": 0.0,
                "source": "legacy_migration",
                "transaction_id": "",
            })
    data["version"] = 3
    return data


# 读取时自动迁移旧版本，并在落盘前保留时间戳备份。
def load_data():
    if not os.path.exists(DATA_FILE):
        data = _default_data()
        save_data(data)
        return data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            raw = json.load(file)
        old_version = raw.get("version", 1)
        data = _migrate(raw)
        if old_version < 3:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = os.path.join(
                os.path.dirname(DATA_FILE),
                f"portfolio_data.backup_{timestamp}.json",
            )
            if not os.path.exists(backup):
                with open(backup, "w", encoding="utf-8") as file:
                    json.dump(raw, file, ensure_ascii=False, indent=2)
            save_data(data)
            logger.info(
                "Migrated portfolio data from version %s to 3; backup=%s",
                old_version, backup,
            )
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("读取组合数据失败")
        raise RuntimeError(f"读取 portfolio_data.json 失败：{exc}") from exc


# 临时文件写完后原子替换，避免写入中断破坏主数据文件。
def save_data(data):
    temp_path = DATA_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, DATA_FILE)


# 适配旧分析函数所需的四元组格式。
def portfolio_tuples(data=None):
    data = data or load_data()
    return [
        (item["name"], item["category"], float(item["amount"]), float(item["profit"]))
        for item in data["holdings"] if float(item.get("amount", 0)) > 0
    ]


# 调用统一估值服务并保存兼容字段及组合摘要。
def update_holdings_market_values(provider=None, refresh=True):
    """Mark holdings to the latest NAV and persist valuation metadata.

    Legacy holdings without units receive an inferred valuation unit baseline on
    the first run. This preserves their current value while allowing subsequent
    NAV updates to change market value and profit.
    """
    from position_service import recalculate_positions
    provider = provider or build_default_provider()
    data = load_data()
    positions, portfolio = recalculate_positions(data, provider, refresh)
    save_data(data)
    results = [{
        "code": p.fund_code, "name": p.fund_name,
        "status": "error" if p.data_quality == "读取失败" else "updated",
        "latest_nav": p.latest_nav, "latest_date": p.latest_nav_date,
        "amount": p.current_value, "profit": p.profit_amount,
        "unit_source": p.valuation_method, "data_source": p.data_source,
        "warning_list": p.warning_list,
        "error": p.warning_list[-1] if p.data_quality == "读取失败" else "",
    } for p in positions]
    data["position_summary"] = portfolio
    save_data(data)
    return data, results


# 旧式手工持仓入口仅用于兼容，精确持仓应优先通过批次维护。
def upsert_holding(code, category, amount, profit):
    data = load_data()
    fund = get_fund(code=code)
    if not fund:
        raise ValueError("基金不在基金池中")
    holding = next((item for item in data["holdings"] if item.get("code") == code), None)
    values = {
        "name": fund["name"], "code": code,
        "category": category or fund.get("category", "未分类"),
        "amount": round(float(amount), 2), "profit": round(float(profit), 2),
        "manual_amount": round(float(amount), 2),
        "manual_profit": round(float(profit), 2),
        "total_cost": round(float(amount) - float(profit), 2),
        "valuation_method": "manual_fallback",
        "data_quality": "待估值",
        "valuation_warning_list": [
            "历史持仓金额为估算录入，请运行分析或补录真实买入批次"
        ],
    }
    if holding:
        holding.update(values)
    else:
        values["units"] = 0.0
        data["holdings"].append(values)
    save_data(data)
    return data


def delete_holding(name):
    data = load_data()
    data["holdings"] = [item for item in data["holdings"] if item["name"] != name]
    save_data(data)
    return data


# 按基金代码同步清除持仓、交易和批次，供基金池删除确认流程调用。
def delete_fund_records(fund_code):
    """Delete all portfolio records owned by a fund code."""
    data = load_data()
    holdings_before = len(data["holdings"])
    transactions_before = len(data["transactions"])
    lots_before = len(data["lots"])
    data["holdings"] = [
        item for item in data["holdings"] if item.get("code") != fund_code
    ]
    data["transactions"] = [
        item for item in data["transactions"] if item.get("fund_code") != fund_code
    ]
    data["lots"] = [
        item for item in data["lots"] if item.get("fund_code") != fund_code
    ]
    save_data(data)
    return data, {
        "holdings": holdings_before - len(data["holdings"]),
        "transactions": transactions_before - len(data["transactions"]),
        "lots": lots_before - len(data["lots"]),
    }


# 根据所有剩余批次重新汇总基金份额和成本。
def _rebuild_holdings(data):
    legacy = {h["code"]: h for h in data["holdings"]}
    for code, holding in legacy.items():
        lots = [lot for lot in data["lots"] if lot["fund_code"] == code]
        transaction_lots = [
            lot for lot in lots if lot.get("source") != "legacy_migration"
        ]
        known_units = sum(float(lot.get("remaining_units", 0)) for lot in transaction_lots)
        known_cost = 0.0
        for lot in transaction_lots:
            confirmed = float(lot.get("confirmed_units", 0))
            remaining = float(lot.get("remaining_units", 0))
            original_cost = float(lot.get("buy_amount", 0)) + float(lot.get("fee", 0))
            # 已部分卖出的批次按剩余份额比例保留成本。
            known_cost += original_cost * remaining / confirmed if confirmed > 0 else 0
        legacy_cost = sum(
            float(lot.get("buy_amount", 0)) + float(lot.get("fee", 0))
            for lot in lots if lot.get("source") == "legacy_migration"
        )
        legacy_units = float(holding.get("legacy_estimated_units", 0))
        holding["transaction_units"] = round(known_units, 8)
        holding["units"] = round(known_units, 4)
        holding["valuation_units"] = round(known_units + legacy_units, 8)
        holding["valuation_unit_source"] = (
            "mixed_estimated" if legacy_units > 0 else "transaction_units"
        )
        holding["total_cost"] = round(known_cost + legacy_cost, 2)
        holding["profit"] = round(
            float(holding.get("amount", 0)) - float(holding.get("total_cost", 0)), 2
        )


# 登记确认交易；卖出按买入日期顺序消耗批次份额。
def add_transaction(fund_code, action, confirm_date, amount, shares=0, nav=0, fee=0, note=""):
    if action not in {"买入", "卖出"}:
        raise ValueError("交易类型必须是买入或卖出")
    datetime.strptime(confirm_date, "%Y-%m-%d")
    amount, units, nav, fee = map(float, (amount, shares or 0, nav or 0, fee or 0))
    if amount <= 0:
        raise ValueError("确认金额必须大于 0")
    if units <= 0 and nav > 0:
        # 交易录入沿用净申购金额口径，手续费不换算为基金份额。
        units = max((amount - fee) / nav, 0)
    if units <= 0:
        raise ValueError("请填写确认份额，或填写确认净值以自动计算份额")
    if nav > 0:
        # 与最近净值进行量级校验，拦截把金额误填为净值等明显错误。
        try:
            latest = build_default_provider().get_fund_nav(fund_code, refresh=False)
            latest_nav = float(pd.to_numeric(latest.price["netvalue"], errors="coerce").dropna().iloc[-1])
            ratio = nav / latest_nav
            if ratio > 3 or ratio < 1 / 3:
                raise ValueError(
                    f"确认净值 {nav:g} 与最近净值 {latest_nav:g} 量级不一致，请核对录入"
                )
        except DataProviderError:
            pass
    data = load_data()
    fund = get_fund(code=fund_code)
    if not fund:
        raise ValueError("基金不在基金池中")
    name = fund["name"]
    holding = next((item for item in data["holdings"] if item["code"] == fund["code"]), None)
    if not holding:
        holding = {
            "name": name, "code": fund["code"], "category": fund["category"],
            "amount": 0.0, "profit": 0.0, "units": 0.0, "total_cost": 0.0,
        }
        data["holdings"].append(holding)
    tx_id = uuid.uuid4().hex
    if action == "买入":
        holding["amount"] = round(float(holding["amount"]) + amount, 2)
        data["lots"].append({
            "lot_id": uuid.uuid4().hex, "fund_code": fund["code"], "fund_name": name,
            "buy_date": confirm_date, "buy_amount": round(amount, 2),
            "confirmed_nav": round(nav, 4), "confirmed_units": round(units, 4),
            "remaining_units": round(units, 4), "fee": round(fee, 2),
            "source": "transaction", "transaction_id": tx_id,
        })
    else:
        if amount > float(holding["amount"]):
            raise ValueError("卖出金额不能大于当前持仓金额")
        remaining = units
        # 卖出使用 FIFO，确保剩余批次和持有天数仍可追踪。
        for lot in sorted(
            [x for x in data["lots"] if x["fund_code"] == fund["code"]
             and float(x.get("remaining_units", 0)) > 0],
            key=lambda x: x.get("buy_date", ""),
        ):
            used = min(remaining, float(lot["remaining_units"]))
            lot["remaining_units"] = round(float(lot["remaining_units"]) - used, 4)
            remaining -= used
            if remaining <= 0:
                break
        if remaining > 0.0001:
            raise ValueError("可用 lot 份额不足，无法完成卖出")
        holding["amount"] = round(float(holding["amount"]) - amount, 2)
    data["transactions"].append({
        "id": tx_id, "fund_name": name, "fund_code": fund["code"], "action": action,
        "confirm_date": confirm_date, "amount": round(amount, 2),
        "shares": round(units, 4), "nav": round(nav, 4),
        "confirmed_units": round(units, 4), "confirmed_nav": round(nav, 4),
        "fee": round(fee, 2), "note": note.strip(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _rebuild_holdings(data)
    save_data(data)
    return data


# 仅允许直接撤销买入；卖出撤销会影响 FIFO 链条，需通过更正处理。
def delete_transaction(transaction_id):
    data = load_data()
    tx = next((item for item in data["transactions"] if item["id"] == transaction_id), None)
    if not tx:
        return data
    if tx["action"] == "卖出":
        raise ValueError("卖出交易涉及 FIFO lot，当前版本请通过更正交易处理，不能直接撤销")
    holding = next((h for h in data["holdings"] if h["code"] == tx["fund_code"]), None)
    if holding and float(holding["amount"]) < float(tx["amount"]):
        raise ValueError("当前持仓不足，无法撤销该买入")
    data["lots"] = [lot for lot in data["lots"] if lot.get("transaction_id") != transaction_id]
    if holding:
        holding["amount"] = round(float(holding["amount"]) - float(tx["amount"]), 2)
    data["transactions"] = [item for item in data["transactions"] if item["id"] != transaction_id]
    _rebuild_holdings(data)
    save_data(data)
    return data


# 修复已有批次时以确认金额和净值反算份额，冲突时以金额为准。
def update_lot(
    lot_id, buy_date, confirmed_nav, confirmed_amount,
    confirmed_units=None, fee=0, note="",
):
    data = load_data()
    lot = next((item for item in data["lots"] if item.get("lot_id") == lot_id), None)
    if not lot:
        raise ValueError("未找到持仓批次")
    datetime.strptime(buy_date, "%Y-%m-%d")
    nav = float(confirmed_nav)
    amount = float(confirmed_amount)
    fee = float(fee or 0)
    if nav <= 0 or amount <= 0:
        raise ValueError("确认净值和确认金额必须大于0")
    if fee < 0:
        raise ValueError("手续费不能小于0")
    calculated_units = amount / nav
    entered_units = (
        float(confirmed_units)
        if confirmed_units not in (None, "")
        else None
    )
    if entered_units is not None and entered_units <= 0:
        raise ValueError("填写确认份额时，确认份额必须大于0")
    units = calculated_units
    lot.update({
        "buy_date": buy_date,
        "confirmed_nav": round(nav, 6),
        "confirmed_units": round(units, 4),
        "remaining_units": round(units, 4),
        "fee": round(fee, 2),
        "buy_amount": round(amount, 2),
        "note": note.strip(),
        "source": "manual_repaired",
        "unit_calculation": "confirmed_amount_div_confirmed_nav",
        "entered_confirmed_units": (
            round(entered_units, 4) if entered_units is not None else None
        ),
    })
    holding = next(
        (item for item in data["holdings"] if item.get("code") == lot["fund_code"]), None
    )
    if holding:
        holding["legacy_estimated_units"] = 0.0
    _rebuild_holdings(data)
    save_data(data)
    return data


# 新增独立买入批次，同一基金可拥有多次不同日期的买入记录。
def add_purchase_lot(
    fund_code, buy_date, confirmed_nav, confirmed_amount,
    confirmed_units=None, fee=0, note="",
):
    """Add one manually confirmed purchase lot and rebuild the holding."""
    data = load_data()
    fund = get_fund(code=fund_code)
    if not fund:
        raise ValueError("基金不在基金池中")
    fund_name = fund["name"]
    datetime.strptime(buy_date, "%Y-%m-%d")
    nav = float(confirmed_nav)
    amount = float(confirmed_amount)
    fee = float(fee or 0)
    if nav <= 0 or amount <= 0:
        raise ValueError("确认净值和确认金额必须大于0")
    if fee < 0:
        raise ValueError("手续费不能小于0")
    entered_units = (
        float(confirmed_units)
        if confirmed_units not in (None, "")
        else None
    )
    if entered_units is not None and entered_units <= 0:
        raise ValueError("填写确认份额时，确认份额必须大于0")
    units = amount / nav
    holding = next(
        (item for item in data["holdings"] if item.get("code") == fund["code"]), None
    )
    if not holding:
        holding = {
            "name": fund["name"], "code": fund["code"],
            "category": fund.get("category", "未分类"),
            "amount": 0.0, "profit": 0.0, "units": 0.0, "total_cost": 0.0,
            "legacy_estimated_units": 0.0,
        }
        data["holdings"].append(holding)
    data["lots"].append({
        "lot_id": uuid.uuid4().hex,
        "fund_code": fund["code"],
        "fund_name": fund["name"],
        "buy_date": buy_date,
        "buy_amount": round(amount, 2),
        "confirmed_nav": round(nav, 6),
        "confirmed_units": round(units, 4),
        "remaining_units": round(units, 4),
        "fee": round(fee, 2),
        "note": note.strip(),
        "source": "manual_purchase",
        "transaction_id": "",
        "unit_calculation": "confirmed_amount_div_confirmed_nav",
        "entered_confirmed_units": (
            round(entered_units, 4) if entered_units is not None else None
        ),
    })
    _rebuild_holdings(data)
    save_data(data)
    return data


# 删除无交易流水关联的手工批次，并在无剩余批次时清理空持仓。
def delete_lot(lot_id):
    """Delete one purchase lot and remove an empty holding."""
    data = load_data()
    lot = next((item for item in data["lots"] if item.get("lot_id") == lot_id), None)
    if not lot:
        raise ValueError("未找到持仓批次")
    if lot.get("transaction_id"):
        raise ValueError("该批次关联交易流水，请在交易流水中撤销对应买入")
    code = lot.get("fund_code")
    data["lots"] = [item for item in data["lots"] if item.get("lot_id") != lot_id]
    _rebuild_holdings(data)
    remaining = [
        item for item in data["lots"]
        if item.get("fund_code") == code and float(item.get("remaining_units", 0)) > 0
    ]
    if not remaining:
        data["holdings"] = [
            item for item in data["holdings"] if item.get("code") != code
        ]
    save_data(data)
    return data


# 将批次数据转换为 GUI 表格行，并附带估值质量和缺失信息。
def lot_rows(data=None):
    data = data or load_data()
    today = date.today()
    rows = []
    holdings = {h["code"]: h for h in data["holdings"]}
    for lot in data.get("lots", []):
        buy_date = lot.get("buy_date", "")
        holding_days = (today - datetime.strptime(buy_date, "%Y-%m-%d").date()).days if buy_date else ""
        units = float(lot.get("remaining_units", 0))
        holding = holdings.get(lot["fund_code"], {})
        latest_nav = float(holding.get("latest_nav", 0))
        is_legacy = lot.get("source") == "legacy_migration"
        if units > 0 and latest_nav > 0:
            current_value = units * latest_nav
            quality = "精确" if lot.get("buy_date") and lot.get("confirmed_nav") else "不完整"
        elif is_legacy and float(holding.get("valuation_units", 0)) > 0:
            units = float(holding["valuation_units"])
            current_value = float(holding.get("current_value", holding.get("amount", 0)))
            quality = "估算"
        else:
            current_value = 0
            quality = "数据不足"
        cost = float(lot.get("buy_amount", 0)) + float(lot.get("fee", 0))
        profit = current_value - cost if current_value else 0
        warnings = []
        if not buy_date:
            warnings.append("买入日期未知，无法判断持有天数/赎回费")
        if is_legacy:
            warnings.append("旧数据迁移，份额为估算值")
        rows.append({
            "lot_id": lot["lot_id"], "基金名称": lot["fund_name"], "基金代码": lot["fund_code"],
            "买入日期": buy_date or "旧数据迁移", "买入金额": lot.get("buy_amount", 0),
            "确认净值": lot.get("confirmed_nav", 0), "剩余份额": units,
            "手续费": lot.get("fee", 0), "持有天数": holding_days,
            "当前市值": round(current_value, 2), "浮动盈亏": round(profit, 2),
            "最新净值": holding.get("latest_nav", ""),
            "数据质量": quality,
            "估值方式": holding.get("valuation_method", "estimated" if is_legacy else ""),
            "缺失信息": "；".join(warnings) or "无",
            "赎回费估算": "无法估算" if not buy_date else "待配置费率",
        })
    return rows


# 将统一估值结果转换为持仓明细和 CSV 共用字段。
def position_rows(data=None):
    data = data or load_data()
    total = sum(float(item.get("current_value", item.get("amount", 0)) or 0)
                for item in data["holdings"])
    rows = []
    for item in data["holdings"]:
        value = float(item.get("current_value", item.get("amount", 0)) or 0)
        if value <= 0:
            continue
        cost = float(item.get("total_cost", 0))
        profit = item.get("profit_amount", item.get("profit"))
        rate = item.get("profit_rate")
        rows.append({
            "基金名称": item["name"], "基金代码": item["code"],
            "资产类别": item["category"],
            "最新净值": item.get("latest_nav", ""),
            "净值日期": item.get("latest_nav_date", ""),
            "确认/估算份额": item.get("valuation_units", item.get("units", "")),
            "当前市值": round(value, 2), "持仓成本": round(cost, 2),
            "当前收益": round(float(profit), 2) if profit is not None else "N/A",
            "当前收益率": f"{float(rate) * 100:.2f}%" if rate is not None else "N/A",
            "持仓占比": f"{value / total * 100:.1f}%" if total else "0.0%",
            "数据质量": item.get("data_quality", "旧数据"),
            "估值方式": item.get("valuation_method", "manual_fallback"),
            "数据来源": item.get("valuation_source", ""),
        })
    return rows
