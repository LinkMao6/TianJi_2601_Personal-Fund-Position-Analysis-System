"""Application-wide rotating file logging."""

import logging
import os
from logging.handlers import RotatingFileHandler


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "xalpha_portfolio.log")


# 统一配置轮转日志，避免长期运行时单个日志文件无限增长。
def configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger("xalpha_portfolio")
    if root.handlers:
        return root
    root.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    return root


# 子模块统一挂载到同一日志命名空间，便于按模块追踪数据链路。
def get_logger(name):
    configure_logging()
    return logging.getLogger(f"xalpha_portfolio.{name}")
