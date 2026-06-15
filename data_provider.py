"""Fund data provider abstraction with local CSV cache."""

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from app_logging import get_logger
from fund_registry import get_fund, validate_code


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_DIR, "data_xalpha", "cache")
STATUS_FILE = os.path.join(CACHE_DIR, "provider_status.json")
logger = get_logger(__name__)


# 数据源统一异常类型，上层据此决定提示错误或回退真实缓存。
class DataProviderError(RuntimeError):
    pass


# 抽象接口隔离 xalpha 实现，指标和 GUI 不直接依赖第三方对象。
class DataProvider(ABC):
    @abstractmethod
    def get_fund_info(self, code):
        raise NotImplementedError

    @abstractmethod
    def get_fund_nav(self, code, refresh=True):
        raise NotImplementedError


# FundData 同时携带净值表和来源状态，供日志及数据质量判断使用。
class FundData:
    def __init__(self, name, price, source, updated_at, stale=False):
        self.name = name
        self.price = price
        self.source = source
        self.updated_at = updated_at
        self.stale = stale


# xalpha 远程适配器只负责获取原始基金信息，不承担缓存策略。
class XalphaProvider(DataProvider):
    def __init__(self):
        try:
            import xalpha
        except ImportError as exc:
            raise DataProviderError(
                "缺少 xalpha 依赖，请执行 pip install -r requirements.txt"
            ) from exc
        self.xalpha = xalpha

    def get_fund_info(self, code):
        code = validate_code(code)
        fund = get_fund(code=code) or {}
        # 货币基金和普通基金在 xalpha 中使用不同的信息对象。
        cls = self.xalpha.mfundinfo if fund.get("fund_type") == "money" else self.xalpha.fundinfo
        try:
            info = cls(code)
            _ = info.price
            return info
        except Exception as exc:
            raise DataProviderError(f"获取基金 {code} 失败：{exc}") from exc

    def get_fund_nav(self, code, refresh=True):
        info = self.get_fund_info(code)
        price = info.price.copy()
        price = self._append_latest_published_nav(code, price)
        return FundData(
            info.name, price, "xalpha",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _append_latest_published_nav(self, code, price):
        """补入详情页已正式公布、但历史净值接口尚未同步的单位净值。"""
        try:
            # xalpha 对天天基金详情页有 10 分钟缓存；用户主动刷新时清除它。
            from xalpha import universal

            cached_fetch = getattr(universal, "get_rt_from_ttjj", None)
            if cached_fetch and hasattr(cached_fetch, "cache_clear"):
                cached_fetch.cache_clear()
            latest = self.xalpha.get_rt(f"F{code}", _from="ttjj")
            latest_date = pd.to_datetime(latest.get("time"), errors="coerce")
            latest_nav = pd.to_numeric(latest.get("current"), errors="coerce")
            if pd.isna(latest_date) or pd.isna(latest_nav) or float(latest_nav) <= 0:
                return price

            result = price.copy()
            result["date"] = pd.to_datetime(result["date"])
            history_date = result["date"].max()
            if pd.isna(history_date) or latest_date.normalize() <= history_date.normalize():
                return price

            row = {column: None for column in result.columns}
            row["date"] = latest_date.normalize()
            row["netvalue"] = float(latest_nav)
            if "comment" in row:
                row["comment"] = 0
            # 详情页只提供正式单位净值，累计净值保持缺失，避免推算伪造。
            result = pd.concat([result, pd.DataFrame([row])], ignore_index=True)
            result = result.drop_duplicates("date", keep="last").sort_values("date")
            logger.info(
                "Appended latest published NAV code=%s date=%s netvalue=%s",
                code, latest_date.date().isoformat(), latest_nav,
            )
            return result
        except Exception as exc:
            # 详情页补全失败不影响历史净值主链路。
            logger.warning("Latest published NAV lookup failed code=%s: %s", code, exc)
            return price


# 缓存适配器负责远程更新、增量合并和失败回退。
class CachedProvider(DataProvider):
    def __init__(self, remote=None):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.remote = remote

    def _path(self, code):
        return os.path.join(CACHE_DIR, f"{validate_code(code)}.csv")

    def _record_status(self, code, status, source, message=""):
        # 数据中心读取该状态文件，向用户展示最近一次更新结果。
        statuses = {}
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as file:
                    statuses = json.load(file)
            except (OSError, json.JSONDecodeError):
                statuses = {}
        statuses[code] = {
            "status": status, "source": source, "message": message,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        temp = STATUS_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(statuses, file, ensure_ascii=False, indent=2)
        os.replace(temp, STATUS_FILE)

    def get_fund_info(self, code):
        if not self.remote:
            raise DataProviderError("远程数据源不可用")
        return self.remote.get_fund_info(code)

    def get_fund_nav(self, code, refresh=True):
        code = validate_code(code)
        path = self._path(code)
        # refresh=True 表示用户主动要求联网刷新，不能被近期缓存短路。
        # 同一轮分析的后续计算统一使用 refresh=False 读取刚写入的缓存。
        if refresh and self.remote:
            try:
                result = self.remote.get_fund_nav(code)
                price = result.price.copy()
                if os.path.exists(path):
                    # 按日期合并新旧数据，实现增量更新并保留更长历史。
                    cached = pd.read_csv(path, encoding="utf-8-sig")
                    if "date" in cached.columns and "date" in price.columns:
                        price = pd.concat([cached, price], ignore_index=True)
                        price["date"] = pd.to_datetime(price["date"])
                        price = price.drop_duplicates("date", keep="last").sort_values("date")
                        price["date"] = price["date"].dt.strftime("%Y-%m-%d")
                price.to_csv(path, index=False, encoding="utf-8-sig")
                result.price = price
                self._record_status(code, "ok", "xalpha")
                return result
            except DataProviderError as exc:
                logger.warning("%s", exc)
                error = str(exc)
        else:
            error = "未请求远程更新"
        if os.path.exists(path):
            # 远程失败时仅使用此前下载的真实净值，不生成随机替代结果。
            price = pd.read_csv(path, encoding="utf-8-sig")
            fund = get_fund(code=code) or {}
            updated = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
            if refresh:
                self._record_status(code, "cached", "local_cache", error)
            return FundData(
                fund.get("name", code), price, "本地缓存", updated,
                stale=bool(refresh),
            )
        self._record_status(code, "error", "none", error)
        # 没有远程结果也没有缓存时明确失败，避免把不可用数据当成真实结果。
        raise DataProviderError(f"{error}；本地也没有基金 {code} 的缓存")


# 默认数据源由“xalpha 远程 + 本地缓存”组合而成。
def build_default_provider():
    try:
        remote = XalphaProvider()
    except DataProviderError as exc:
        logger.warning("%s", exc)
        remote = None
    return CachedProvider(remote)


def load_provider_status():
    if not os.path.exists(STATUS_FILE):
        return {}
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
