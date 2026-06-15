"""Persistent dynamic fund pool."""

import json
import os
import re
from datetime import datetime

from app_logging import get_logger


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FUND_POOL_FILE = os.path.join(PROJECT_DIR, "fund_pool.json")
logger = get_logger(__name__)


# 基金代码作为跨模块主键，入口处统一校验可避免缓存和持仓关联错位。
def validate_code(code):
    code = str(code).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("基金代码必须是 6 位数字")
    return code


# 读取时补齐旧配置缺少的字段，保持基金池文件向后兼容。
def load_fund_pool():
    with open(FUND_POOL_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("version", 1)
    data.setdefault("funds", [])
    for fund in data["funds"]:
        fund.setdefault("category", "未分类")
        fund.setdefault("fund_type", "normal")
        fund.setdefault("enabled", True)
        fund.setdefault("data_source", "xalpha")
        fund.setdefault("is_dca", False)
        fund.setdefault("dca_frequency", "monthly")
        fund.setdefault("dca_base_amount", 0.0)
        fund.setdefault("dca_allow_pause", True)
        fund.setdefault("dca_allow_increase", True)
        fund.setdefault("dca_max_multiplier", 2.0)
        fund.setdefault("dca_note", "")
    return data


# 先写临时文件再原子替换，降低程序中断造成配置损坏的概率。
def save_fund_pool(data):
    temp = FUND_POOL_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp, FUND_POOL_FILE)


# enabled_only 用于分析和下拉列表过滤，停用基金仍保留历史配置。
def list_funds(enabled_only=False):
    funds = load_fund_pool()["funds"]
    return [fund for fund in funds if fund.get("enabled", True)] if enabled_only else funds


# 允许按代码或名称查找，业务关联优先使用稳定的六位基金代码。
def get_fund(code=None, name=None):
    for fund in list_funds():
        if code and fund["code"] == str(code):
            return fund
        if name and fund["name"] == name:
            return fund
    return None


# 将基金池转换为旧分析接口仍可使用的“名称到代码”映射。
def fund_codes(enabled_only=False):
    return {fund["name"]: fund["code"] for fund in list_funds(enabled_only)}


# 新增与编辑共用同一入口，并完整保留定投规则字段。
def upsert_fund(code, name, category="未分类", fund_type="normal",
                enabled=True, data_source="xalpha", **dca):
    code = validate_code(code)
    name = str(name).strip()
    if not name:
        raise ValueError("基金名称不能为空")
    if fund_type not in {"normal", "money"}:
        raise ValueError("基金类型必须是 normal 或 money")
    data = load_fund_pool()
    fund = next((item for item in data["funds"] if item["code"] == code), None)
    duplicate_name = next(
        (item for item in data["funds"]
         if item["name"] == name and item["code"] != code),
        None,
    )
    if duplicate_name:
        raise ValueError(
            f"基金名称“{name}”已被代码 {duplicate_name['code']} 使用，"
            "请使用包含份额类别的唯一名称"
        )
    values = {
        "code": code,
        "name": name,
        "category": str(category).strip() or "未分类",
        "fund_type": fund_type,
        "enabled": bool(enabled),
        "data_source": data_source,
        "is_dca": bool(dca.get("is_dca", (fund or {}).get("is_dca", False))),
        "dca_frequency": dca.get("dca_frequency", (fund or {}).get("dca_frequency", "monthly")),
        "dca_base_amount": float(dca.get("dca_base_amount", (fund or {}).get("dca_base_amount", 0))),
        "dca_allow_pause": bool(dca.get("dca_allow_pause", (fund or {}).get("dca_allow_pause", True))),
        "dca_allow_increase": bool(dca.get("dca_allow_increase", (fund or {}).get("dca_allow_increase", True))),
        "dca_max_multiplier": float(dca.get("dca_max_multiplier", (fund or {}).get("dca_max_multiplier", 2))),
        "dca_note": str(dca.get("dca_note", (fund or {}).get("dca_note", ""))).strip(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if fund:
        fund.update(values)
    else:
        values["created_at"] = values["updated_at"]
        data["funds"].append(values)
    save_fund_pool(data)
    logger.info("Saved fund %s %s", code, name)
    return values


# 此函数只删除基金池配置；持仓、交易和批次由上层确认后同步处理。
def delete_fund(code):
    code = validate_code(code)
    data = load_fund_pool()
    data["funds"] = [fund for fund in data["funds"] if fund["code"] != code]
    save_fund_pool(data)


# 启停基金时复用原定投配置，避免状态切换覆盖用户参数。
def set_fund_enabled(code, enabled):
    fund = get_fund(code=validate_code(code))
    if not fund:
        raise ValueError("基金不存在")
    dca = {key: fund[key] for key in (
        "is_dca", "dca_frequency", "dca_base_amount", "dca_allow_pause",
        "dca_allow_increase", "dca_max_multiplier", "dca_note"
    )}
    return upsert_fund(
        fund["code"], fund["name"], fund["category"], fund["fund_type"],
        enabled, fund.get("data_source", "xalpha"), **dca,
    )
