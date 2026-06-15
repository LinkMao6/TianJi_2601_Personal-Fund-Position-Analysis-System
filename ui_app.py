# -*- coding: utf-8 -*-
"""天玑个人基金组合分析与回测系统 V1.0 桌面应用。"""

import datetime
import ctypes
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from app_info import APP_FULL_NAME
from data_provider import DataProviderError, build_default_provider, load_provider_status
from fund_registry import (
    delete_fund, get_fund, list_funds, set_fund_enabled, upsert_fund,
)
from portfolio_store import (
    add_purchase_lot,
    add_transaction,
    delete_holding,
    delete_fund_records,
    delete_lot,
    delete_transaction,
    load_data,
    lot_rows,
    position_rows,
    update_lot,
    portfolio_tuples,
    upsert_holding,
)
from run_portfolio import OUTPUT_DIR, run_full_analysis
from quant_service import run_backtests
from recommendation import RecommendationService


COLORS = {
    "navy": "#0B1220",
    "navy_2": "#111C30",
    "blue": "#4F7CFF",
    "cyan": "#34D1BF",
    "green": "#27C499",
    "red": "#F0646E",
    "amber": "#F4B860",
    "purple": "#9B7EDE",
    "bg": "#F3F6FB",
    "card": "#FFFFFF",
    "text": "#182033",
    "muted": "#738096",
    "line": "#E3E9F2",
}

CHART_COLORS = [
    COLORS["blue"], COLORS["cyan"], COLORS["amber"],
    COLORS["purple"], COLORS["green"], COLORS["red"],
]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# 启用 Windows DPI 感知，避免系统位图缩放导致文字和图表模糊。
def enable_windows_dpi_awareness():
    """Prevent Windows from bitmap-scaling the entire Tk window."""
    if sys.platform != "win32":
        return

    try:
        # Per-monitor V2: remains sharp when moved between monitors.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# 根据屏幕实际 DPI 调整 Tk 缩放比例，保持不同分辨率下布局一致。
def configure_tk_dpi(root):
    """Match Tk point sizes to the actual DPI of the current monitor."""
    if sys.platform != "win32":
        return

    root.update_idletasks()
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id())
    except (AttributeError, OSError):
        dpi = int(root.winfo_fpixels("1i"))

    if dpi > 0:
        root.tk.call("tk", "scaling", dpi / 72.0)


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable ttk frame."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style="Page.TFrame")
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _sync_scrollregion(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _on_mousewheel(self, event):
        if self.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


# 桌面应用负责界面状态和用户交互，业务计算由下层服务完成。
class PortfolioAnalyzerApp:
    """Modern Tkinter dashboard for portfolio analysis."""

    def __init__(self, root):
        self.root = root
        self.root.title(APP_FULL_NAME)
        self.root.geometry("1440x900")
        self.root.minsize(1120, 720)
        self.root.configure(bg=COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.task_queue = queue.Queue()
        self.analysis_running = False
        self.worker_thread = None
        self.chart_canvases = []
        self.metric_values = {}
        self.store_data = load_data()
        self.portfolio_data = portfolio_tuples(self.store_data)
        self.data_provider = build_default_provider()
        self.recommendation_service = RecommendationService(self.data_provider)
        self.recommendations = []

        self._configure_styles()
        self._build_ui()
        self.process_queue()
        self._load_existing_results()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei", 10))
        style.configure("Page.TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("Header.TFrame", background=COLORS["navy"])
        style.configure("Header.TLabel", background=COLORS["navy"], foreground="white")
        style.configure("MutedHeader.TLabel", background=COLORS["navy"], foreground="#98A7C0")
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                        font=("Microsoft YaHei", 17, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["muted"],
                        font=("Microsoft YaHei", 9))
        style.configure("CardValue.TLabel", background=COLORS["card"], foreground=COLORS["text"],
                        font=("Microsoft YaHei", 20, "bold"))
        style.configure("Accent.TButton", background=COLORS["blue"], foreground="white",
                        padding=(18, 10), borderwidth=0, font=("Microsoft YaHei", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#3E68DD"), ("disabled", "#94A7D8")])
        style.configure("Ghost.TButton", background=COLORS["navy_2"], foreground="#DDE6F5",
                        padding=(14, 9), borderwidth=0)
        style.map("Ghost.TButton", background=[("active", "#1C2B47")])
        style.configure("Dash.TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("Dash.TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["muted"],
                        padding=(18, 11), borderwidth=0, font=("Microsoft YaHei", 10, "bold"))
        style.map("Dash.TNotebook.Tab",
                  background=[("selected", COLORS["card"])],
                  foreground=[("selected", COLORS["blue"])])
        style.configure("Dash.Treeview", background=COLORS["card"], fieldbackground=COLORS["card"],
                        foreground=COLORS["text"], rowheight=34, borderwidth=0)
        style.configure("Dash.Treeview.Heading", background="#EAF0FA", foreground="#43516A",
                        padding=(8, 9), borderwidth=0, font=("Microsoft YaHei", 9, "bold"))
        style.map("Dash.Treeview", background=[("selected", "#DDE7FF")],
                  foreground=[("selected", COLORS["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor="#26334A",
                        background=COLORS["cyan"], borderwidth=0)

    def _build_ui(self):
        self._create_header()
        shell = ttk.Frame(self.root, style="Page.TFrame", padding=(24, 18, 24, 20))
        shell.pack(fill="both", expand=True)

        heading = ttk.Frame(shell, style="Page.TFrame")
        heading.pack(fill="x", pady=(0, 12))
        ttk.Label(heading, text="投资组合仪表盘", style="Title.TLabel").pack(side="left")
        self.updated_label = ttk.Label(
            heading, text="等待数据", style="Subtitle.TLabel"
        )
        self.updated_label.pack(side="right", pady=6)

        self.notebook = ttk.Notebook(shell, style="Dash.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.tab_dashboard = ttk.Frame(self.notebook, style="Page.TFrame")
        self.tab_holdings = self._create_holdings_tab()
        self.tab_transactions = self._create_table_tab("交易流水")
        self.tab_lots = self._create_table_tab("持仓批次")
        self.tab_funds = self._create_table_tab("基金池管理")
        self.tab_data = self._create_table_tab("数据中心")
        self.tab_risk = self._create_table_tab("收益与风险")
        self.tab_dca = self._create_table_tab("定投模拟")
        self.tab_indicators = self._create_quant_tab()
        self.tab_backtest = self._create_backtest_tab()
        self.tab_recommendation = self._create_recommendation_tab()
        self.tab_log = self._create_log_tab()
        self.notebook.add(self.tab_dashboard, text="  总览与图表  ")
        self.notebook.add(self.tab_holdings, text="  持仓明细  ")
        self.notebook.add(self.tab_transactions, text="  交易流水  ")
        self.notebook.add(self.tab_lots, text="  持仓批次  ")
        self.notebook.add(self.tab_funds, text="  基金池管理  ")
        self.notebook.add(self.tab_data, text="  数据中心  ")
        self.notebook.add(self.tab_risk, text="  收益与风险  ")
        self.notebook.add(self.tab_dca, text="  定投模拟  ")
        self.notebook.add(self.tab_indicators, text="  量化指标  ")
        self.notebook.add(self.tab_backtest, text="  策略回测  ")
        self.notebook.add(self.tab_recommendation, text="  策略建议  ")
        self.notebook.add(self.tab_log, text="  运行日志  ")
        self._create_dashboard()
        self._create_portfolio_actions()
        self._create_fund_pool_actions()
        self._create_lot_actions()

    def _create_holdings_tab(self):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=12)
        pane = ttk.Panedwindow(frame, orient="vertical")
        pane.pack(fill="both", expand=True)

        table_card = ttk.Frame(pane, style="Card.TFrame", padding=10)
        tree = ttk.Treeview(table_card, show="headings", style="Dash.Treeview", height=11)
        vsb = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(table_card, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)

        chart_card = ttk.Frame(pane, style="Card.TFrame", padding=10)
        toolbar = ttk.Frame(chart_card, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        self.holding_chart_title = ttk.Label(
            toolbar, text="基金净值趋势", style="CardTitle.TLabel",
            font=("Microsoft YaHei", 11, "bold"),
        )
        self.holding_chart_title.pack(side="left")
        self.holding_chart_status = ttk.Label(
            toolbar, text="请选择上方基金", style="CardTitle.TLabel"
        )
        self.holding_chart_status.pack(side="left", padx=14)
        self.holding_chart_period = tk.StringVar(value="3m")
        period_box = ttk.Frame(toolbar, style="Card.TFrame")
        period_box.pack(side="right")
        for label, value in [("近1月", "1m"), ("近3月", "3m"), ("近1年", "1y")]:
            ttk.Radiobutton(
                period_box, text=label, value=value, variable=self.holding_chart_period,
                command=self.render_holding_nav_chart,
            ).pack(side="left", padx=4)
        self.holding_chart_host = tk.Frame(chart_card, bg=COLORS["card"])
        self.holding_chart_host.pack(fill="both", expand=True)

        pane.add(table_card, weight=2)
        pane.add(chart_card, weight=3)
        frame._tree = tree
        tree.bind("<<TreeviewSelect>>", self.on_holding_selected)
        self.holding_nav_data = None
        self.holding_nav_code = None
        self.holding_nav_name = None
        self.holding_nav_request = 0
        self._render_holding_chart_message("请选择上方持仓基金查看净值趋势")
        return frame

    def _render_holding_chart_message(self, message):
        fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "净值趋势")
        ax.text(0.5, 0.5, message, ha="center", va="center",
                color=COLORS["muted"], transform=ax.transAxes, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        self._replace_tab_chart(self.holding_chart_host, fig)

    # 选中持仓后异步读取净值，避免网络或磁盘操作阻塞界面。
    def on_holding_selected(self, _event=None):
        selected = self.tab_holdings._tree.selection()
        if not selected:
            return
        columns = list(self.tab_holdings._tree["columns"])
        values = self.tab_holdings._tree.item(selected[0], "values")
        try:
            code = str(values[columns.index("基金代码")]).zfill(6)
            name = str(values[columns.index("基金名称")])
        except (ValueError, IndexError):
            return
        if code == self.holding_nav_code and self.holding_nav_data is not None:
            self.render_holding_nav_chart()
            return
        self.holding_nav_request += 1
        request_id = self.holding_nav_request
        self.holding_nav_code = code
        self.holding_nav_name = name
        self.holding_nav_data = None
        self.holding_chart_title.configure(text=f"{name}（{code}）净值趋势")
        self.holding_chart_status.configure(text="正在读取净值数据...")
        self._render_holding_chart_message("正在读取净值数据...")

        def worker():
            try:
                result = self.data_provider.get_fund_nav(code, refresh=False)
                self.task_queue.put(("holding_nav_done", (request_id, result)))
            except Exception as exc:
                self.task_queue.put(("holding_nav_error", (request_id, str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def render_holding_nav_chart(self):
        if self.holding_nav_data is None:
            return
        data = self.holding_nav_data.copy()
        data["date"] = pd.to_datetime(data["date"])
        value_col = "totvalue" if "totvalue" in data.columns else "netvalue"
        data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
        data = data.dropna(subset=[value_col]).sort_values("date")
        days = {"1m": 31, "3m": 93, "1y": 366}[self.holding_chart_period.get()]
        cutoff = data["date"].max() - pd.Timedelta(days=days)
        view = data[data["date"] >= cutoff]
        if view.empty:
            self._render_holding_chart_message("所选周期暂无净值数据")
            return
        base = view[value_col].iloc[0]
        return_rate = (view[value_col].iloc[-1] / base - 1) * 100 if base else 0
        color = COLORS["red"] if return_rate >= 0 else COLORS["green"]
        fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "累计净值走势")
        ax.plot(view["date"], view[value_col], color=color, linewidth=1.8)
        ax.fill_between(view["date"], view[value_col], view[value_col].min(),
                        color=color, alpha=0.10)
        ax.text(
            0.99, 0.95, f"区间涨跌  {return_rate:+.2f}%",
            transform=ax.transAxes, ha="right", va="top", color=color,
            fontsize=11, fontweight="bold",
        )
        ax.set_ylabel("累计净值" if value_col == "totvalue" else "单位净值",
                      color=COLORS["muted"], fontsize=8)
        ax.grid(axis="both", color=COLORS["line"], linewidth=0.7, alpha=0.7)
        fig.autofmt_xdate()
        fig.tight_layout(pad=1.5)
        self._replace_tab_chart(self.holding_chart_host, fig)

    def _create_recommendation_tab(self):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=10)
        controls = ttk.Frame(frame, style="Page.TFrame")
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="更新数据并生成", command=lambda: self.start_recommendations(False, True)).pack(side="left", padx=4)
        ttk.Button(controls, text="生成全部基金建议", style="Accent.TButton",
                   command=lambda: self.start_recommendations(False, False)).pack(side="left", padx=4)
        ttk.Button(controls, text="仅生成持仓基金建议",
                   command=lambda: self.start_recommendations(True, False)).pack(side="left", padx=4)
        ttk.Button(controls, text="导出建议报告",
                   command=self.export_recommendations).pack(side="left", padx=4)
        ttk.Label(
            controls, text="分析与建议仅供个人学习、记录和研究参考，不构成投资建议、收益承诺或交易指令。",
            foreground=COLORS["red"],
        ).pack(side="right", padx=8)
        pane = ttk.Panedwindow(frame, orient="vertical")
        pane.pack(fill="both", expand=True)
        table = self._embedded_tree(pane)
        detail_holder = ttk.Frame(pane, style="Page.TFrame")
        self.recommendation_canvas = tk.Canvas(
            detail_holder, bg=COLORS["bg"], highlightthickness=0
        )
        detail_scroll = ttk.Scrollbar(
            detail_holder, orient="vertical", command=self.recommendation_canvas.yview
        )
        self.recommendation_cards = tk.Frame(self.recommendation_canvas, bg=COLORS["bg"])
        self.recommendation_cards_window = self.recommendation_canvas.create_window(
            (0, 0), window=self.recommendation_cards, anchor="nw"
        )
        self.recommendation_canvas.configure(yscrollcommand=detail_scroll.set)
        self.recommendation_canvas.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.recommendation_cards.bind(
            "<Configure>",
            lambda _event: self.recommendation_canvas.configure(
                scrollregion=self.recommendation_canvas.bbox("all")
            ),
        )
        self.recommendation_canvas.bind(
            "<Configure>",
            lambda event: self.recommendation_canvas.itemconfigure(
                self.recommendation_cards_window, width=event.width
            ),
        )
        pane.add(table, weight=3)
        pane.add(detail_holder, weight=2)
        frame._tree = table._tree
        frame._tree.bind("<<TreeviewSelect>>", self.show_recommendation_detail)
        self._render_recommendation_empty()
        return frame

    def _recommendation_card(self, parent, title, accent=COLORS["blue"]):
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightthickness=1,
            highlightbackground=COLORS["line"], padx=14, pady=11,
        )
        tk.Frame(card, bg=accent, height=3).pack(fill="x", pady=(0, 8))
        tk.Label(
            card, text=title, bg=COLORS["card"], fg=COLORS["muted"],
            font=("Microsoft YaHei", 9, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        body = tk.Frame(card, bg=COLORS["card"])
        body.pack(fill="both", expand=True)
        return card, body

    def _card_value(self, parent, value, color=COLORS["text"], size=16):
        tk.Label(
            parent, text=value, bg=COLORS["card"], fg=color,
            font=("Microsoft YaHei", size, "bold"), justify="left", anchor="w",
            wraplength=330,
        ).pack(fill="x", anchor="w")

    def _card_lines(self, parent, lines, color=COLORS["text"]):
        text = "\n".join(f"• {line}" for line in (lines or ["无"]))
        tk.Label(
            parent, text=text, bg=COLORS["card"], fg=color,
            font=("Microsoft YaHei", 9), justify="left", anchor="nw",
            wraplength=620,
        ).pack(fill="both", expand=True, anchor="w")

    def _clear_recommendation_cards(self):
        for child in self.recommendation_cards.winfo_children():
            child.destroy()

    def _render_recommendation_empty(self):
        self._clear_recommendation_cards()
        card, body = self._recommendation_card(
            self.recommendation_cards, "策略建议详情", COLORS["cyan"]
        )
        card.pack(fill="x", padx=6, pady=6)
        tk.Label(
            body, text="请先生成建议，并在上方表格中选择一只基金。",
            bg=COLORS["card"], fg=COLORS["muted"],
            font=("Microsoft YaHei", 11), pady=18,
        ).pack()

    def _create_quant_tab(self):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=3)
        frame.rowconfigure(1, weight=2)
        table = self._embedded_tree(frame)
        table.grid(row=0, column=0, sticky="nsew")
        chart = tk.Frame(frame, bg=COLORS["card"])
        chart.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        frame._tree = table._tree
        frame._chart = chart
        return frame

    def _create_backtest_tab(self):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=10)
        controls = ttk.Frame(frame, style="Page.TFrame")
        controls.pack(fill="x", pady=(0, 8))
        funds = list_funds(enabled_only=True)
        self.backtest_fund = tk.StringVar(value=funds[0]["name"] if funds else "")
        self.backtest_start = tk.StringVar(value="2023-01-01")
        self.backtest_end = tk.StringVar(value=datetime.date.today().isoformat())
        self.backtest_frequency = tk.StringVar(value="monthly")
        self.backtest_amount = tk.StringVar(value="100")
        for text, widget in [
            ("基金", ttk.Combobox(controls, textvariable=self.backtest_fund,
                                  values=[f["name"] for f in funds], state="readonly", width=28)),
            ("开始", ttk.Entry(controls, textvariable=self.backtest_start, width=12)),
            ("结束", ttk.Entry(controls, textvariable=self.backtest_end, width=12)),
            ("频率", ttk.Combobox(controls, textvariable=self.backtest_frequency,
                                  values=["daily", "weekly", "monthly"], state="readonly", width=9)),
            ("金额", ttk.Entry(controls, textvariable=self.backtest_amount, width=9)),
        ]:
            ttk.Label(controls, text=text).pack(side="left", padx=(6, 3))
            widget.pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="运行回测", style="Accent.TButton",
                   command=self.start_custom_backtest).pack(side="left", padx=10)
        body = ttk.Panedwindow(frame, orient="vertical")
        body.pack(fill="both", expand=True)
        table = self._embedded_tree(body)
        chart = tk.Frame(body, bg=COLORS["card"])
        body.add(table, weight=2)
        body.add(chart, weight=3)
        frame._tree = table._tree
        frame._chart = chart
        return frame

    def _embedded_tree(self, parent):
        holder = ttk.Frame(parent, style="Card.TFrame")
        tree = ttk.Treeview(holder, show="headings", style="Dash.Treeview")
        ybar = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        holder._tree = tree
        return holder

    def _create_header(self):
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(26, 17))
        header.pack(fill="x")
        brand = ttk.Frame(header, style="Header.TFrame")
        brand.pack(side="left")
        ttk.Label(
            brand, text=APP_FULL_NAME, style="Header.TLabel",
            font=("Microsoft YaHei", 15, "bold"),
        ).pack(anchor="w")
        profile = self.store_data.get("data_profile", "user")
        ttk.Label(
            brand,
            text="V1.0 模拟演示数据" if profile == "demo" else "V1.0 用户数据",
            style="MutedHeader.TLabel",
            font=("Microsoft YaHei", 9),
        ).pack(anchor="w", pady=(2, 0))

        actions = ttk.Frame(header, style="Header.TFrame")
        actions.pack(side="right")
        self.status_dot = tk.Label(actions, text="●", bg=COLORS["navy"], fg=COLORS["cyan"],
                                   font=("Segoe UI", 11))
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_label = ttk.Label(actions, text="就绪", style="MutedHeader.TLabel")
        self.status_label.pack(side="left", padx=(0, 18))
        self.btn_output = ttk.Button(actions, text="打开输出", style="Ghost.TButton",
                                     command=self.open_output_folder)
        self.btn_output.pack(side="left", padx=4)
        self.btn_report = ttk.Button(actions, text="查看报告", style="Ghost.TButton",
                                     command=self.open_report)
        self.btn_report.pack(side="left", padx=4)
        self.btn_trade = ttk.Button(actions, text="登记交易", style="Ghost.TButton",
                                    command=self.open_transaction_dialog)
        self.btn_trade.pack(side="left", padx=4)
        self.btn_edit = ttk.Button(
            actions, text="持仓批次管理", style="Ghost.TButton",
            command=self.open_lot_manager_dialog,
        )
        self.btn_edit.pack(side="left", padx=4)
        self.btn_start = ttk.Button(actions, text="开始分析", style="Accent.TButton",
                                    command=self.start_analysis)
        self.btn_start.pack(side="left", padx=(12, 0))

        progress_row = ttk.Frame(self.root, style="Header.TFrame")
        progress_row.pack(fill="x")
        self.progress_bar = ttk.Progressbar(progress_row, mode="determinate", value=0)
        self.progress_bar.pack(fill="x")

    def _create_dashboard(self):
        scroll = ScrollableFrame(self.tab_dashboard)
        scroll.pack(fill="both", expand=True)
        self.dashboard_body = scroll.body

        metrics = ttk.Frame(self.dashboard_body, style="Page.TFrame")
        metrics.pack(fill="x", pady=(4, 16))
        for i in range(6):
            metrics.columnconfigure(i, weight=1, uniform="metric")
        cards = [
            ("total_assets", "组合总资产", "--", COLORS["blue"]),
            ("total_profit", "累计收益", "--", COLORS["red"]),
            ("return_rate", "组合收益率", "--", COLORS["cyan"]),
            ("profit_count", "盈利基金", "--", COLORS["red"]),
            ("loss_count", "亏损基金", "--", COLORS["green"]),
            ("asset_count", "持仓数量", str(len(self.portfolio_data)), COLORS["amber"]),
        ]
        for i, (key, title, value, accent) in enumerate(cards):
            card = tk.Frame(metrics, bg=COLORS["card"], highlightthickness=1,
                            highlightbackground=COLORS["line"], padx=14, pady=13)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0 if i == 5 else 5))
            tk.Frame(card, bg=accent, height=3).pack(fill="x", pady=(0, 10))
            tk.Label(card, text=title, bg=COLORS["card"], fg=COLORS["muted"],
                     font=("Microsoft YaHei", 9)).pack(anchor="w")
            label = tk.Label(card, text=value, bg=COLORS["card"], fg=COLORS["text"],
                             font=("Microsoft YaHei", 18, "bold"))
            label.pack(anchor="w", pady=(4, 0))
            self.metric_values[key] = label

        section = ttk.Frame(self.dashboard_body, style="Page.TFrame")
        section.pack(fill="x", pady=(0, 8))
        ttk.Label(section, text="组合洞察", style="Title.TLabel").pack(side="left")
        ttk.Label(
            section, text="图表按最近一次成功分析结果绘制",
            style="Subtitle.TLabel",
        ).pack(side="right", pady=6)

        self.charts_grid = ttk.Frame(self.dashboard_body, style="Page.TFrame")
        self.charts_grid.pack(fill="both", expand=True)
        for col in range(2):
            self.charts_grid.columnconfigure(col, weight=1, uniform="chart")
        self._render_empty_charts()

    def _create_portfolio_actions(self):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="持仓批次管理", command=self.open_lot_manager_dialog)
        menu.add_command(label="登记确认交易", command=self.open_transaction_dialog)
        menu.add_separator()
        menu.add_command(label="撤销选中交易", command=self.delete_selected_transaction)
        self.portfolio_menu = menu
        self.tab_transactions._tree.bind("<Delete>", lambda _event: self.delete_selected_transaction())

    def _create_fund_pool_actions(self):
        bar = ttk.Frame(self.tab_funds, style="Page.TFrame")
        bar.pack(fill="x", before=self.tab_funds.winfo_children()[0], pady=(0, 8))
        ttk.Button(bar, text="添加基金", style="Accent.TButton",
                   command=self.open_fund_dialog).pack(side="left", padx=4)
        ttk.Button(bar, text="编辑选中", command=self.edit_selected_fund).pack(side="left", padx=4)
        ttk.Button(bar, text="启用 / 停用", command=self.toggle_selected_fund).pack(side="left", padx=4)
        ttk.Button(bar, text="删除选中", command=self.delete_selected_fund).pack(side="left", padx=4)

    def _create_lot_actions(self):
        bar = ttk.Frame(self.tab_lots, style="Page.TFrame")
        bar.pack(fill="x", before=self.tab_lots.winfo_children()[0], pady=(0, 8))
        ttk.Button(
            bar, text="持仓批次管理", style="Accent.TButton",
            command=self.open_lot_manager_dialog,
        ).pack(side="left", padx=4)

    def _create_table_tab(self, _title):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=12)
        card = ttk.Frame(frame, style="Card.TFrame", padding=12)
        card.pack(fill="both", expand=True)
        tree = ttk.Treeview(card, show="headings", style="Dash.Treeview")
        vsb = ttk.Scrollbar(card, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(card, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        frame._tree = tree
        return frame

    def _create_log_tab(self):
        frame = ttk.Frame(self.notebook, style="Page.TFrame", padding=12)
        self.log_text = tk.Text(frame, bg=COLORS["navy"], fg="#D8E2F1",
                                insertbackground="white", relief="flat", padx=16, pady=14,
                                font=("Consolas", 10), state="disabled", wrap="word")
        scrollbar = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return frame

    def _render_empty_charts(self):
        self._clear_charts()
        messages = [
            ("资产配置", "等待持仓配置数据"),
            ("类别近期收益", "等待基金净值数据"),
            ("风险收益矩阵", "等待风险指标数据"),
            ("定投表现", "等待定投模拟数据"),
        ]
        for index, (title, text) in enumerate(messages):
            fig = Figure(figsize=(6.2, 3.35), dpi=100, facecolor=COLORS["card"])
            ax = fig.add_subplot(111)
            self._style_axis(ax, title)
            ax.text(0.5, 0.5, text, ha="center", va="center", color=COLORS["muted"],
                    transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self._mount_chart(fig, index)

    def _clear_charts(self):
        for widget in self.charts_grid.winfo_children():
            widget.destroy()
        self.chart_canvases.clear()

    def _mount_chart(self, fig, index):
        card = tk.Frame(self.charts_grid, bg=COLORS["card"], highlightthickness=1,
                        highlightbackground=COLORS["line"])
        card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=6, pady=6)
        canvas = FigureCanvasTkAgg(fig, master=card)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)
        self.chart_canvases.append(canvas)

    def _style_axis(self, ax, title):
        ax.set_facecolor(COLORS["card"])
        ax.set_title(title, loc="left", fontsize=12, fontweight="bold",
                     color=COLORS["text"], pad=14)
        ax.tick_params(colors=COLORS["muted"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(axis="y", color=COLORS["line"], linewidth=0.7, alpha=0.8)
        ax.set_axisbelow(True)

    # 重新绘制总览图表前销毁旧画布，避免重复挂载占用资源。
    def render_charts(self, period_df=None, risk_df=None, dca_results=None):
        self._clear_charts()
        self._draw_allocation_chart(0)
        self._draw_category_chart(period_df, 1)
        self._draw_risk_chart(risk_df, 2)
        self._draw_dca_chart(dca_results, 3)

    def _draw_allocation_chart(self, index):
        allocation = {}
        for _, category, amount, _ in self.portfolio_data:
            allocation[category] = allocation.get(category, 0) + amount
        fig = Figure(figsize=(6.2, 3.35), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "资产配置")
        wedges, _ = ax.pie(
            allocation.values(), startangle=90, colors=CHART_COLORS[:len(allocation)],
            wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 2}
        )
        total = sum(allocation.values())
        ax.text(0, 0.08, f"CNY {total:,.0f}", ha="center", va="center",
                fontsize=16, fontweight="bold", color=COLORS["text"])
        ax.text(0, -0.13, "组合资产", ha="center", va="center", fontsize=8, color=COLORS["muted"])
        ax.legend(wedges, [f"{k}  {v/total:.0%}" for k, v in allocation.items()],
                  loc="center left", bbox_to_anchor=(0.92, 0.5), frameon=False, fontsize=8)
        self._mount_chart(fig, index)

    def _draw_category_chart(self, df, index):
        fig = Figure(figsize=(6.2, 3.35), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "类别近30日平均收益")
        if df is None or df.empty or "近30日" not in df:
            ax.text(0.5, 0.5, "暂无净值数据", ha="center", va="center",
                    color=COLORS["muted"], transform=ax.transAxes)
        else:
            data = df.copy()
            category_map = {name: category for name, category, _, _ in self.portfolio_data}
            data["类别"] = data["基金名称"].map(category_map)
            data["近30日"] = pd.to_numeric(data["近30日"], errors="coerce")
            grouped = data.dropna(subset=["近30日"]).groupby("类别")["近30日"].mean().sort_values()
            colors = [COLORS["red"] if value >= 0 else COLORS["green"] for value in grouped]
            bars = ax.barh(grouped.index, grouped.values, color=colors, height=0.55)
            ax.axvline(0, color=COLORS["line"], linewidth=1)
            for bar, value in zip(bars, grouped.values):
                ax.text(value + (0.12 if value >= 0 else -0.12),
                        bar.get_y() + bar.get_height() / 2, f"{value:.2f}%",
                        va="center", ha="left" if value >= 0 else "right",
                        fontsize=8, color=COLORS["text"])
            ax.set_xlabel("收益率 (%)", color=COLORS["muted"], fontsize=8)
        fig.tight_layout(pad=2)
        self._mount_chart(fig, index)

    def _draw_risk_chart(self, df, index):
        fig = Figure(figsize=(6.2, 3.35), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "风险收益矩阵")
        if df is None or df.empty:
            ax.text(0.5, 0.5, "暂无风险数据", ha="center", va="center",
                    color=COLORS["muted"], transform=ax.transAxes)
        else:
            data = df[df["数据来源"].isin(["真实", "xalpha", "本地缓存"])].copy()
            for col in ["年化收益率", "年化波动率", "最大回撤"]:
                data[col] = pd.to_numeric(data[col].astype(str).str.replace("%", "", regex=False),
                                          errors="coerce")
            data = data.dropna(subset=["年化收益率", "年化波动率"])
            sizes = 45 + data["最大回撤"].fillna(0) * 3
            point_colors = [
                COLORS["red"] if value >= 0 else COLORS["green"]
                for value in data["年化收益率"]
            ]
            ax.scatter(data["年化波动率"], data["年化收益率"], s=sizes,
                       c=point_colors, alpha=0.82,
                       edgecolors="white", linewidth=1)
            for _, row in data.iterrows():
                label = str(row["基金名称"]).replace("ETF联接", "").replace("(QDII)", "")
                ax.annotate(label[:7], (row["年化波动率"], row["年化收益率"]),
                            xytext=(4, 4), textcoords="offset points", fontsize=6.5,
                            color=COLORS["muted"])
            ax.set_xlabel("年化波动率 (%)", color=COLORS["muted"], fontsize=8)
            ax.set_ylabel("年化收益率 (%)", color=COLORS["muted"], fontsize=8)
        fig.tight_layout(pad=2)
        self._mount_chart(fig, index)

    def _draw_dca_chart(self, results, index):
        fig = Figure(figsize=(6.2, 3.35), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "定投收益率对比")
        if not results:
            ax.text(0.5, 0.5, "暂无定投数据", ha="center", va="center",
                    color=COLORS["muted"], transform=ax.transAxes)
        else:
            periods = [180, 365, 730]
            labels = ["半年", "一年", "两年"]
            x = np.arange(len(periods))
            width = 0.34
            for i, (fund, values) in enumerate(results.items()):
                rates = []
                for days in periods:
                    rate = str(values.get(days, {}).get("收益率", "0")).replace("%", "")
                    rates.append(float(rate))
                short_name = fund.replace("ETF联接(QDII)A", "").replace("债券A", "")
                colors = [COLORS["red"] if rate >= 0 else COLORS["green"] for rate in rates]
                ax.bar(x + (i - 0.5) * width, rates, width, label=short_name,
                       color=colors, alpha=0.9)
            ax.set_xticks(x, labels)
            ax.set_ylabel("收益率 (%)", color=COLORS["muted"], fontsize=8)
            ax.legend(frameon=False, fontsize=7, loc="upper left")
        fig.tight_layout(pad=2)
        self._mount_chart(fig, index)

    # 完整分析放入后台线程，Tk 主线程只负责界面更新。
    def start_analysis(self):
        if self.analysis_running:
            return
        self.analysis_running = True
        self.btn_start.configure(state="disabled", text="分析中...")
        self.set_status("正在获取与计算数据", COLORS["amber"])
        self.progress_bar["value"] = 0
        self.clear_log()
        self.append_log("开始执行完整组合分析")
        self.worker_thread = threading.Thread(target=self.worker_run_analysis, daemon=True)
        self.worker_thread.start()

    # 工作线程只写入队列，不直接调用 Tk 控件，避免跨线程更新异常。
    def worker_run_analysis(self):
        def log_cb(message):
            self.task_queue.put(("log", message))

        def progress_cb(value):
            self.task_queue.put(("progress", value))

        try:
            result = run_full_analysis(
                progress_callback=progress_cb,
                log_callback=log_cb,
                portfolio=self.portfolio_data,
            )
            self.task_queue.put(("done", result))
        except Exception as exc:
            self.task_queue.put(("error", f"{exc}\n{traceback.format_exc()}"))

    # 主线程定时消费任务队列并刷新进度、表格、图表和错误提示。
    def process_queue(self):
        try:
            while True:
                kind, value = self.task_queue.get_nowait()
                if kind == "log":
                    self.append_log(value)
                elif kind == "progress":
                    self.progress_bar["value"] = int(value)
                elif kind == "done":
                    self._on_analysis_done(value)
                elif kind == "error":
                    self._on_analysis_error(value)
                elif kind == "backtest_done":
                    self.analysis_running = False
                    self.btn_start.configure(state="normal")
                    summary, curve = value
                    self._fill_tree(self.tab_backtest._tree, summary)
                    self._render_backtest_chart(curve)
                    self.set_status("回测完成", COLORS["cyan"])
                elif kind == "backtest_error":
                    self.analysis_running = False
                    self.btn_start.configure(state="normal")
                    self.set_status("回测失败", COLORS["red"])
                    messagebox.showerror("回测失败", str(value))
                elif kind == "recommendations_done":
                    self.analysis_running = False
                    self.btn_start.configure(state="normal")
                    self.recommendations = value
                    self._fill_recommendation_table()
                    self.set_status("建议生成完成", COLORS["cyan"])
                    self.append_log(f"已生成 {len(value)} 条基金建议")
                elif kind == "recommendations_error":
                    self.analysis_running = False
                    self.btn_start.configure(state="normal")
                    self.set_status("建议生成失败", COLORS["red"])
                    messagebox.showerror("建议生成失败", str(value))
                elif kind == "holding_nav_done":
                    request_id, result = value
                    if request_id == self.holding_nav_request:
                        self.holding_nav_data = result.price
                        source = result.source
                        stale = "，缓存数据" if result.stale else ""
                        self.holding_chart_status.configure(
                            text=f"数据来源：{source}{stale}  更新：{result.updated_at}"
                        )
                        self.render_holding_nav_chart()
                elif kind == "holding_nav_error":
                    request_id, message = value
                    if request_id == self.holding_nav_request:
                        self.holding_chart_status.configure(text="净值数据不可用")
                        self._render_holding_chart_message(f"无法读取净值数据：{message}")
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def _on_analysis_done(self, result):
        self.analysis_running = False
        self.btn_start.configure(state="normal", text="重新分析")
        self.progress_bar["value"] = 100
        status = result.get("_status", "success")
        if status == "failed":
            self.set_status("分析失败", COLORS["red"])
            self.append_log("本轮分析发生致命错误，请查看日志")
        elif status == "partial":
            self.set_status("分析部分完成", COLORS["amber"])
            failed = len(result.get("_errors", []))
            missing = len(result.get("_missing_outputs", []))
            self.append_log(f"本轮分析部分完成：失败步骤 {failed} 项，未生成输出 {missing} 项")
        else:
            self.set_status("分析完成", COLORS["cyan"])
        self.updated_label.configure(text=f"最近更新  {datetime.datetime.now():%Y-%m-%d %H:%M}")
        self.store_data = load_data()
        self.portfolio_data = portfolio_tuples(self.store_data)
        self.load_results(result)
        self.append_log("界面数据与图表已刷新")

    def _on_analysis_error(self, message):
        self.analysis_running = False
        self.btn_start.configure(state="normal", text="重试分析")
        self.set_status("分析失败", COLORS["red"])
        self.append_log(message)
        messagebox.showerror("分析失败", message[:600])

    def load_results(self, result):
        payload = result.get("_data", {})
        summary = payload.get("summary")
        period_df = payload.get("period_returns")
        risk_df = payload.get("risk")
        dca_results = payload.get("dca")
        indicator_df = payload.get("indicators")
        portfolio_nav = payload.get("portfolio_nav")
        backtest_df = payload.get("backtest")
        backtest_curve = payload.get("backtest_curve")

        if summary:
            self._set_metrics(summary)
        self._load_csv_table(self.tab_holdings._tree, result.get("holdings_result"))
        self._load_transaction_table()
        self._load_lot_table()
        self._load_fund_pool_table()
        self._load_data_center()
        self._load_risk_table(self.tab_risk._tree, period_df, risk_df)
        dca_df = self._dca_to_dataframe(dca_results)
        self._fill_tree(self.tab_dca._tree, dca_df)
        self._fill_tree(self.tab_indicators._tree, indicator_df)
        self._render_indicator_chart(indicator_df, portfolio_nav)
        self._fill_tree(self.tab_backtest._tree, backtest_df)
        self._render_backtest_chart(backtest_curve)
        self.render_charts(period_df, risk_df, dca_results)

    def _replace_tab_chart(self, holder, fig):
        for child in holder.winfo_children():
            child.destroy()
        canvas = FigureCanvasTkAgg(fig, master=holder)
        widget = canvas.get_tk_widget()
        widget.pack(fill="both", expand=True)
        holder._canvas = canvas
        holder._figure = fig

        if not getattr(holder, "_chart_resize_bound", False):
            holder.bind("<Configure>", lambda event, host=holder: self._resize_host_chart(host, event))
            holder._chart_resize_bound = True

        # Tk widgets report 1x1 until the current geometry pass has completed.
        self.root.after_idle(lambda host=holder: self._resize_host_chart(host))

    def _resize_host_chart(self, holder, event=None):
        canvas = getattr(holder, "_canvas", None)
        fig = getattr(holder, "_figure", None)
        if canvas is None or fig is None or not holder.winfo_exists():
            return
        width = event.width if event is not None else holder.winfo_width()
        height = event.height if event is not None else holder.winfo_height()
        if width < 20 or height < 20:
            self.root.after(50, lambda host=holder: self._resize_host_chart(host))
            return
        dpi = fig.get_dpi()
        target = (width / dpi, height / dpi)
        current = fig.get_size_inches()
        if abs(current[0] - target[0]) > 0.02 or abs(current[1] - target[1]) > 0.02:
            fig.set_size_inches(*target, forward=False)
        canvas.draw_idle()

    def _render_indicator_chart(self, indicators, portfolio_nav):
        fig = Figure(figsize=(10, 3.5), dpi=100, facecolor=COLORS["card"])
        left, right = fig.subplots(1, 2)
        self._style_axis(left, "基金收益风险分布")
        self._style_axis(right, "组合净值")
        if indicators is not None and not indicators.empty:
            funds = indicators[indicators["对象类型"] == "基金"].copy()
            x = pd.to_numeric(funds["年化波动率"], errors="coerce")
            y = pd.to_numeric(funds["年化收益率"], errors="coerce")
            left.scatter(x, y, color=COLORS["blue"], alpha=0.8)
            left.set_xlabel("年化波动率(%)")
            left.set_ylabel("年化收益率(%)")
        if portfolio_nav is not None and not portfolio_nav.empty:
            right.plot(pd.to_datetime(portfolio_nav["date"]), portfolio_nav["portfolio_nav"],
                       color=COLORS["cyan"], linewidth=1.5)
        fig.tight_layout()
        self._replace_tab_chart(self.tab_indicators._chart, fig)

    def _render_backtest_chart(self, curve):
        fig = Figure(figsize=(10, 3.5), dpi=100, facecolor=COLORS["card"])
        ax = fig.add_subplot(111)
        self._style_axis(ax, "策略资金曲线")
        if curve is not None and not curve.empty:
            for strategy, data in curve.groupby("策略"):
                ax.plot(pd.to_datetime(data["date"]), data["equity"], label=strategy)
            ax.legend(frameon=False)
            ax.set_ylabel("资产")
        fig.tight_layout()
        self._replace_tab_chart(self.tab_backtest._chart, fig)

    def start_custom_backtest(self):
        if self.analysis_running:
            return
        fund = next(
            (item for item in list_funds(enabled_only=True)
             if item["name"] == self.backtest_fund.get()),
            None,
        )
        if not fund:
            messagebox.showerror("参数错误", "请选择有效基金")
            return
        try:
            amount = float(self.backtest_amount.get())
            datetime.date.fromisoformat(self.backtest_start.get())
            datetime.date.fromisoformat(self.backtest_end.get())
            if amount <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "日期格式应为 YYYY-MM-DD，金额必须大于 0")
            return
        self.analysis_running = True
        self.btn_start.configure(state="disabled")
        self.set_status("正在运行策略回测", COLORS["amber"])

        def worker():
            try:
                result = run_backtests(
                    fund["code"], self.backtest_start.get(), self.backtest_end.get(),
                    amount, self.backtest_frequency.get(),
                )
                self.task_queue.put(("backtest_done", result))
            except Exception as exc:
                self.task_queue.put(("backtest_error", exc))
        threading.Thread(target=worker, daemon=True).start()

    def start_recommendations(self, only_holdings=False, refresh_data=False):
        if self.analysis_running:
            return
        self.analysis_running = True
        self.btn_start.configure(state="disabled")
        self.set_status("正在生成策略建议", COLORS["amber"])

        def worker():
            try:
                result = self.recommendation_service.generate_recommendations(
                    only_holdings=only_holdings, refresh_data=refresh_data
                )
                self.task_queue.put(("recommendations_done", result))
            except Exception as exc:
                self.task_queue.put(("recommendations_error", exc))
        threading.Thread(target=worker, daemon=True).start()

    def _fill_recommendation_table(self):
        rows = [{
            "基金代码": rec.fund_code, "基金名称": rec.fund_name,
            "基金类型": rec.fund_type, "是否定投": "是" if rec.is_dca else "否",
            "当前建议": rec.action_label,
            "建议金额": rec.suggested_amount if rec.suggested_amount is not None else "N/A",
            "卖出信号": rec.sell_signal, "信号分": rec.signal_score,
            "风险等级": rec.risk_level, "置信度": rec.confidence,
            "数据日期": rec.data_quality.nav_latest_date,
        } for rec in self.recommendations]
        self._fill_tree(self.tab_recommendation._tree, pd.DataFrame(rows))
        children = self.tab_recommendation._tree.get_children()
        if children:
            self.tab_recommendation._tree.selection_set(children[0])
            self.tab_recommendation._tree.focus(children[0])
            self.tab_recommendation._tree.see(children[0])
            self.show_recommendation_detail()
        else:
            self._render_recommendation_empty()

    def show_recommendation_detail(self, _event=None):
        selected = self.tab_recommendation._tree.selection()
        if not selected:
            return
        code = str(self.tab_recommendation._tree.item(selected[0], "values")[0])
        rec = next((item for item in self.recommendations if item.fund_code == code), None)
        if not rec:
            return
        self._clear_recommendation_cards()
        container = self.recommendation_cards
        container.columnconfigure(0, weight=1, uniform="recommendation")
        container.columnconfigure(1, weight=1, uniform="recommendation")

        header = tk.Frame(container, bg=COLORS["navy_2"], padx=18, pady=13)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 4))
        tk.Label(
            header, text=f"{rec.fund_name}（{rec.fund_code}）",
            bg=COLORS["navy_2"], fg="white",
            font=("Microsoft YaHei", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            header, text=f"{'定投基金' if rec.is_dca else '普通基金'}  |  数据日期 {rec.data_quality.nav_latest_date or 'N/A'}",
            bg=COLORS["navy_2"], fg="#AFC0DD",
            font=("Microsoft YaHei", 9),
        ).pack(side="right")

        action_color = COLORS["red"] if rec.signal_score >= 65 else (
            COLORS["green"] if rec.signal_score < 40 else COLORS["blue"]
        )
        cards = [
            ("当前建议", rec.action_label, action_color),
            ("建议金额", "N/A" if rec.suggested_amount is None else f"¥{rec.suggested_amount:,.2f}", COLORS["cyan"]),
            ("卖出信号", f"{rec.sell_signal}  ·  {rec.sell_ratio:.0%}", COLORS["amber"]),
            ("信号评分", f"{rec.signal_score:.1f} / 100", COLORS["purple"]),
            ("风险等级", rec.risk_level, COLORS["red"] if rec.risk_level == "高" else COLORS["amber"]),
            ("置信度", rec.confidence, COLORS["cyan"]),
        ]
        metrics_row = tk.Frame(container, bg=COLORS["bg"])
        metrics_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        for index, (title, value, accent) in enumerate(cards):
            metrics_row.columnconfigure(index, weight=1, uniform="metric")
            card, body = self._recommendation_card(metrics_row, title, accent)
            card.grid(row=0, column=index, sticky="nsew", padx=4, pady=4)
            self._card_value(body, value, accent, 13)

        indicator_labels = {
            "return_20d": "近20日收益", "return_60d": "近60日收益",
            "return_120d": "近120日收益", "return_250d": "近250日收益",
            "rsi14": "RSI14", "dd252": "近252日回撤",
            "annual_return": "年化收益", "volatility": "年化波动",
            "max_drawdown": "最大回撤", "sharpe": "夏普比率", "calmar": "卡玛比率",
        }
        percent_keys = {
            "return_20d", "return_60d", "return_120d", "return_250d",
            "dd252", "annual_return", "volatility", "max_drawdown",
        }
        indicator_lines = []
        for key, label in indicator_labels.items():
            value = rec.indicators.get(key)
            if value is None:
                continue
            try:
                formatted = f"{float(value):.2%}" if key in percent_keys else f"{float(value):.2f}"
            except (TypeError, ValueError):
                formatted = str(value)
            indicator_lines.append(f"{label}：{formatted}")

        sections = [
            ("关键指标", indicator_lines, COLORS["blue"]),
            ("触发规则", rec.triggered_rules, COLORS["cyan"]),
            ("未触发规则", rec.untriggered_rules, COLORS["muted"]),
            ("建议原因", rec.reason_list, COLORS["purple"]),
            ("风险提示", rec.warning_list, COLORS["red"]),
            ("数据质量", [
                f"数据来源：{rec.data_quality.data_source or 'N/A'}",
                f"是否缓存：{'是' if rec.data_quality.is_cached else '否'}",
                f"是否过期：{'是' if rec.data_quality.is_stale else '否'}",
                f"错误信息：{rec.data_quality.error_message or '无'}",
            ], COLORS["amber"]),
        ]
        for index, (title, lines, accent) in enumerate(sections):
            card, body = self._recommendation_card(container, title, accent)
            row, col = 2 + index // 2, index % 2
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=5)
            self._card_lines(body, lines, COLORS["red"] if title == "风险提示" else COLORS["text"])
        self.recommendation_canvas.yview_moveto(0)

    def export_recommendations(self):
        if not self.recommendations:
            messagebox.showinfo("提示", "请先生成建议。")
            return
        path = self.recommendation_service.export_recommendation_report(self.recommendations)
        self.append_log(f"建议报告已导出：{path}")
        if os.path.exists(path):
            os.startfile(path)

    def _set_metrics(self, summary):
        self.metric_values["total_assets"].configure(
            text=f"¥{summary['total_assets']:,.2f}"
        )
        profit_color = COLORS["red"] if summary["total_profit"] >= 0 else COLORS["green"]
        self.metric_values["total_profit"].configure(
            text=f"¥{summary['total_profit']:+,.2f}", fg=profit_color
        )
        self.metric_values["return_rate"].configure(
            text=f"{summary['total_return_rate']:+.2f}%", fg=profit_color)
        self.metric_values["profit_count"].configure(text=str(summary["profit_count"]))
        self.metric_values["loss_count"].configure(text=str(summary["loss_count"]))
        self.metric_values["asset_count"].configure(text=str(len(self.portfolio_data)))

    def _load_csv_table(self, tree, relative_path):
        if not relative_path:
            self._fill_tree(tree, pd.DataFrame())
            return
        path = os.path.join(PROJECT_DIR, relative_path)
        df = pd.read_csv(path, encoding="utf-8-sig") if os.path.exists(path) else pd.DataFrame()
        self._fill_tree(tree, df)
        if tree is self.tab_holdings._tree:
            self._select_default_holding()

    def _select_default_holding(self):
        tree = self.tab_holdings._tree
        children = tree.get_children()
        if not children:
            self.holding_nav_data = None
            self.holding_chart_title.configure(text="基金净值趋势")
            self.holding_chart_status.configure(text="暂无持仓基金")
            self._render_holding_chart_message("暂无持仓基金")
            return
        current_code = self.holding_nav_code
        columns = list(tree["columns"])
        target = children[0]
        if current_code and "基金代码" in columns:
            code_index = columns.index("基金代码")
            for item in children:
                values = tree.item(item, "values")
                if str(values[code_index]).zfill(6) == current_code:
                    target = item
                    break
        tree.selection_set(target)
        tree.focus(target)
        tree.see(target)
        self.on_holding_selected()

    def _load_risk_table(self, tree, period_df, risk_df):
        if period_df is not None and risk_df is not None:
            merge_keys = ["基金名称", "基金代码"]
            if (
                "净值截止日期" in period_df.columns
                and "净值截止日期" in risk_df.columns
            ):
                merge_keys.append("净值截止日期")
            data = pd.merge(period_df, risk_df, on=merge_keys, how="outer")
            if "数据来源_x" in data.columns and "数据来源_y" in data.columns:
                data["数据来源"] = data["数据来源_x"].fillna(data["数据来源_y"])
                data = data.drop(columns=["数据来源_x", "数据来源_y"])
        elif period_df is not None:
            data = period_df
        elif risk_df is not None:
            data = risk_df
        else:
            data = pd.DataFrame()
        self._fill_tree(tree, data)

    def _dca_to_dataframe(self, results):
        rows = []
        labels = {180: "半年", 365: "一年", 730: "两年"}
        for fund, periods in (results or {}).items():
            for days, values in sorted(periods.items()):
                rows.append({
                    "基金名称": fund,
                    "周期": labels.get(days, f"{days}天"),
                    "累计投入": values.get("累计投入", 0),
                    "当前市值": values.get("市值", 0),
                    "收益率": values.get("收益率", "N/A"),
                    "交易天数": values.get("交易天数", ""),
                })
        return pd.DataFrame(rows)

    # DataFrame 统一填充表格，并按收益正负应用红涨绿跌行标签。
    def _fill_tree(self, tree, df):
        tree.delete(*tree.get_children())
        tree["columns"] = []
        if df is None or df.empty:
            return
        columns = list(df.columns)
        tree["columns"] = columns
        for column in columns:
            tree.heading(column, text=column)
            length = max(len(str(column)), int(df[column].astype(str).str.len().max()))
            tree.column(column, width=min(max(length * 11 + 24, 90), 260),
                        anchor="w" if "名称" in column else "center")
        profit_column = self._profit_column(columns)
        for index, (_, row) in enumerate(df.iterrows()):
            profit_state = self._profit_state(row.get(profit_column)) if profit_column else None
            if profit_state == "profit":
                tag = "profit"
            elif profit_state == "loss":
                tag = "loss"
            else:
                tag = "even" if index % 2 == 0 else "odd"
            tree.insert("", "end", values=[row[col] for col in columns],
                        tags=(tag,))
        tree.tag_configure("even", background=COLORS["card"])
        tree.tag_configure("odd", background="#F8FAFD")
        tree.tag_configure("profit", background="#FFF0F1", foreground="#C93645")
        tree.tag_configure("loss", background="#ECFAF5", foreground="#168566")

    @staticmethod
    def _profit_column(columns):
        for column in [
            "当前收益", "持有收益", "收益金额", "当前收益率",
            "收益率", "年化收益率", "近30日",
        ]:
            if column in columns:
                return column
        return None

    @staticmethod
    def _profit_state(value):
        try:
            number = float(str(value).replace("%", "").replace(",", "").replace("CNY", "").strip())
        except (TypeError, ValueError):
            return None
        if number > 0:
            return "profit"
        if number < 0:
            return "loss"
        return None

    # 启动时加载最近一次输出，使离线状态也能查看历史分析结果。
    def _load_existing_results(self):
        paths = {
            "holdings_result": "output/holdings_result.csv",
            "period_return": "output/period_return.csv",
            "risk_report": "output/risk_report.csv",
            "dca_result": "output/dca_result.csv",
        }
        summary_path = os.path.join(OUTPUT_DIR, "summary.csv")
        if not os.path.exists(summary_path):
            self.append_log("暂无历史结果，点击“开始分析”生成数据")
            return
        try:
            summary_csv = pd.read_csv(summary_path, encoding="utf-8-sig")
            values = dict(zip(summary_csv["metric"], summary_csv["value"]))
            summary = {
                "total_assets": self._money_value(values.get("组合总资产", "0")),
                "total_profit": self._money_value(values.get("组合总收益", "0")),
                "total_return_rate": float(str(values.get("组合收益率", "0")).replace("%", "")),
                "profit_count": int(values.get("盈利基金数量", 0)),
                "loss_count": int(values.get("亏损基金数量", 0)),
            }
            period_df = self._read_output_csv("period_return.csv")
            risk_df = self._read_output_csv("risk_report.csv")
            dca_df = self._read_output_csv("dca_result.csv")
            indicator_df = self._read_output_csv("indicator_dataset.csv")
            portfolio_nav = self._read_output_csv("portfolio_nav.csv")
            backtest_df = self._read_output_csv("backtest_result.csv")
            backtest_curve = self._read_output_csv("backtest_curve.csv")
            self._set_metrics(summary)
            self._load_holdings_from_store()
            self._load_transaction_table()
            self._load_lot_table()
            self._load_fund_pool_table()
            self._load_data_center()
            self._load_risk_table(self.tab_risk._tree, period_df, risk_df)
            self._fill_tree(self.tab_dca._tree, dca_df)
            self._fill_tree(self.tab_indicators._tree, indicator_df)
            self._render_indicator_chart(indicator_df, portfolio_nav)
            self._fill_tree(self.tab_backtest._tree, backtest_df)
            self._render_backtest_chart(backtest_curve)
            dca_results = self._dca_dataframe_to_results(dca_df)
            self.render_charts(period_df, risk_df, dca_results)
            updated = values.get("更新时间", "")
            self.updated_label.configure(text=f"历史数据  {updated}")
            self.append_log("已加载历史分析数据，图表由数据直接绘制")
        except Exception as exc:
            self.append_log(f"加载历史数据失败: {exc}")

    # 持仓或交易变化后重新读取 JSON，并同步所有相关页面。
    def refresh_portfolio_data(self, rerender=True):
        self.store_data = load_data()
        self.portfolio_data = portfolio_tuples(self.store_data)
        self.metric_values["asset_count"].configure(text=str(len(self.portfolio_data)))
        self._load_holdings_from_store()
        self._load_transaction_table()
        self._load_lot_table()
        self._load_fund_pool_table()
        self._load_data_center()
        if rerender:
            period_df = self._read_output_csv("period_return.csv")
            risk_df = self._read_output_csv("risk_report.csv")
            dca_df = self._read_output_csv("dca_result.csv")
            self.render_charts(period_df, risk_df, self._dca_dataframe_to_results(dca_df))

    # 持仓明细始终使用统一估值服务生成的表格字段。
    def _load_holdings_from_store(self):
        self._fill_tree(
            self.tab_holdings._tree, pd.DataFrame(position_rows(self.store_data))
        )
        self._select_default_holding()

    def _load_transaction_table(self):
        rows = []
        for item in sorted(
            self.store_data.get("transactions", []),
            key=lambda row: (row["confirm_date"], row["created_at"]),
            reverse=True,
        ):
            rows.append({
                "交易ID": item["id"],
                "确认日期": item["confirm_date"],
                "基金名称": item["fund_name"],
                "类型": item["action"],
                "确认金额": item["amount"],
                "确认份额": item["shares"],
                "确认净值": item["nav"],
                "手续费": item["fee"],
                "备注": item["note"],
            })
        self._fill_tree(self.tab_transactions._tree, pd.DataFrame(rows))
        if "交易ID" in self.tab_transactions._tree["columns"]:
            self.tab_transactions._tree.column("交易ID", width=0, stretch=False)

    def _load_lot_table(self):
        self._fill_tree(self.tab_lots._tree, pd.DataFrame(lot_rows(self.store_data)))
        if "lot_id" in self.tab_lots._tree["columns"]:
            self.tab_lots._tree.column("lot_id", width=0, stretch=False)

    # 按基金集中管理多笔买入批次，持仓由剩余批次自动重建。
    def open_lot_manager_dialog(self):
        funds = list_funds(enabled_only=True)
        if not funds:
            messagebox.showerror("无法管理持仓", "基金池中没有启用的基金。")
            return
        dialog = self._dialog("持仓批次管理", 980, 620)
        dialog.rowconfigure(2, weight=1)
        labels = [f"{item['code']} | {item['name']}" for item in funds]
        fund_by_label = {label: item for label, item in zip(labels, funds)}
        selected_fund = tk.StringVar(value=labels[0])
        ttk.Label(dialog, text="基金").grid(
            row=0, column=0, sticky="e", padx=(20, 8), pady=16
        )
        selector = ttk.Combobox(
            dialog, textvariable=selected_fund,
            values=labels,
            state="readonly", width=42,
        )
        selector.grid(row=0, column=1, sticky="w", pady=16)
        summary = ttk.Label(dialog, text="", foreground=COLORS["muted"])
        summary.grid(row=1, column=0, columnspan=3, sticky="w", padx=20)

        columns = ("批次ID", "买入日期", "确认金额", "确认净值", "剩余份额", "手续费", "备注")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=15)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=125, anchor="center")
        tree.column("批次ID", width=0, stretch=False)
        tree.column("备注", width=220, anchor="w")
        tree.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=20, pady=12)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        scrollbar.grid(row=2, column=3, sticky="ns", pady=12)
        tree.configure(yscrollcommand=scrollbar.set)

        def reload_rows(*_args):
            self.store_data = load_data()
            tree.delete(*tree.get_children())
            fund = fund_by_label.get(selected_fund.get())
            if not fund:
                return
            rows = [
                item for item in self.store_data.get("lots", [])
                if item.get("fund_code") == fund["code"]
            ]
            for lot in sorted(rows, key=lambda item: item.get("buy_date", "")):
                tree.insert("", "end", values=(
                    lot.get("lot_id", ""), lot.get("buy_date", "旧数据迁移"),
                    lot.get("buy_amount", 0), lot.get("confirmed_nav", 0),
                    lot.get("remaining_units", 0), lot.get("fee", 0),
                    lot.get("note", ""),
                ))
            total_cost = sum(
                float(item.get("buy_amount", 0)) + float(item.get("fee", 0))
                for item in rows
            )
            total_units = sum(float(item.get("remaining_units", 0)) for item in rows)
            summary.configure(
                text=f"共 {len(rows)} 个买入批次    剩余份额 {total_units:.4f}"
                     f"    批次成本 {total_cost:.2f} 元"
            )
            self.refresh_portfolio_data()

        def selected_lot():
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("提示", "请先选择一个批次。", parent=dialog)
                return None
            lot_id = tree.item(selected[0], "values")[0]
            return next(
                (item for item in self.store_data.get("lots", [])
                 if item.get("lot_id") == lot_id), None
            )

        def add():
            fund = fund_by_label[selected_fund.get()]
            self._open_lot_editor(
                fund_code=fund["code"], fund_name=fund["name"], on_saved=reload_rows
            )

        def edit():
            lot = selected_lot()
            if lot:
                self._open_lot_editor(lot=lot, on_saved=reload_rows)

        def remove():
            lot = selected_lot()
            if lot and messagebox.askyesno(
                "删除买入批次", "确认删除选中的买入批次？持仓将自动重建。",
                parent=dialog,
            ):
                try:
                    delete_lot(lot["lot_id"])
                    reload_rows()
                    self.set_status("持仓批次已删除，请重新分析", COLORS["amber"])
                except ValueError as exc:
                    messagebox.showerror("无法删除", str(exc), parent=dialog)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, columnspan=3, pady=18)
        ttk.Button(
            buttons, text="新增买入批次", style="Accent.TButton", command=add
        ).pack(side="left", padx=6)
        ttk.Button(buttons, text="编辑选中批次", command=edit).pack(side="left", padx=6)
        ttk.Button(buttons, text="删除选中批次", command=remove).pack(side="left", padx=6)
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="left", padx=6)
        selector.bind("<<ComboboxSelected>>", reload_rows)
        tree.bind("<Double-1>", lambda _event: edit())
        reload_rows()

    def open_lot_repair_dialog(self):
        selected = self.tab_lots._tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一个持仓批次。")
            return
        lot_id = self.tab_lots._tree.item(selected[0], "values")[0]
        lot = next(
            (item for item in self.store_data.get("lots", [])
             if item.get("lot_id") == lot_id), None
        )
        if not lot:
            messagebox.showerror("错误", "未找到选中的持仓批次。")
            return
        self._open_lot_editor(lot=lot)

    # 新增和编辑共用表单，确认份额由金额和净值统一反算。
    def _open_lot_editor(
        self, lot=None, fund_code=None, fund_name=None, on_saved=None
    ):
        editing = lot is not None
        dialog = self._dialog(
            "编辑买入批次" if editing else "新增买入批次", 600, 660
        )
        fund_code = lot.get("fund_code") if editing else fund_code
        fund_name = lot.get("fund_name") if editing else fund_name
        buy_date = tk.StringVar(value=(lot or {}).get("buy_date", ""))
        if not editing:
            buy_date.set(datetime.date.today().isoformat())
        nav = tk.StringVar(value=str((lot or {}).get("confirmed_nav", 0) or ""))
        amount = tk.StringVar(value=str((lot or {}).get("buy_amount", 0) or ""))
        units = tk.StringVar(value=str((lot or {}).get("confirmed_units", 0) or ""))
        fee = tk.StringVar(value=str((lot or {}).get("fee", 0)))
        note = tk.StringVar(value=(lot or {}).get("note", ""))
        calculated_units = tk.StringVar(value="系统计算份额：请输入确认金额和确认净值")
        fields = [
            ("基金", ttk.Label(dialog, text=fund_name)),
            ("买入日期", ttk.Entry(dialog, textvariable=buy_date, width=36)),
            ("确认净值", ttk.Entry(dialog, textvariable=nav, width=36)),
            ("确认金额", ttk.Entry(dialog, textvariable=amount, width=36)),
            ("确认份额（可选）", ttk.Entry(dialog, textvariable=units, width=36)),
            ("手续费", ttk.Entry(dialog, textvariable=fee, width=36)),
            ("备注", ttk.Entry(dialog, textvariable=note, width=36)),
        ]
        for row, (label, widget) in enumerate(fields):
            self._form_row(dialog, row, label, widget)

        def update_calculated_units(*_args):
            try:
                amount_value = float(amount.get())
                nav_value = float(nav.get())
                if amount_value > 0 and nav_value > 0:
                    calculated_units.set(
                        f"系统计算份额：{amount_value / nav_value:.4f}"
                    )
                    return
            except (TypeError, ValueError):
                pass
            calculated_units.set("系统计算份额：请输入有效的确认金额和确认净值")

        amount.trace_add("write", update_calculated_units)
        nav.trace_add("write", update_calculated_units)
        update_calculated_units()
        ttk.Label(
            dialog, textvariable=calculated_units, foreground=COLORS["blue"]
        ).grid(row=7, column=0, columnspan=2, pady=(10, 2))
        ttk.Label(
            dialog,
            text="确认份额可以留空；若与金额冲突，系统始终以确认金额反算份额。",
            foreground=COLORS["muted"],
        ).grid(row=8, column=0, columnspan=2, pady=(2, 10))

        def save():
            try:
                args = (
                    buy_date.get().strip(), float(nav.get()), float(amount.get()),
                    units.get().strip() or None, float(fee.get() or 0), note.get(),
                )
                if editing:
                    update_lot(lot["lot_id"], *args)
                else:
                    add_purchase_lot(fund_code, *args)
                self.refresh_portfolio_data()
                self.append_log(
                    f"已{'更新' if editing else '新增'}买入批次：{fund_name}"
                )
                self.set_status("持仓批次已修改，请重新分析", COLORS["amber"])
                dialog.destroy()
                if on_saved:
                    on_saved()
            except (ValueError, TypeError) as exc:
                messagebox.showerror("输入错误", str(exc), parent=dialog)
        ttk.Button(
            dialog, text="保存并重建持仓", style="Accent.TButton", command=save
        ).grid(row=9, column=0, columnspan=2, pady=18)

    def _load_fund_pool_table(self):
        rows = [{
            "基金代码": fund["code"], "基金名称": fund["name"],
            "资产类别": fund["category"], "基金类型": fund["fund_type"],
            "状态": "启用" if fund.get("enabled", True) else "停用",
            "数据源": fund.get("data_source", "xalpha"),
            "是否定投": "是" if fund.get("is_dca") else "否",
            "定投频率": fund.get("dca_frequency", ""),
            "基础金额": fund.get("dca_base_amount", 0),
        } for fund in list_funds()]
        self._fill_tree(self.tab_funds._tree, pd.DataFrame(rows))

    def _load_data_center(self):
        statuses = load_provider_status()
        rows = []
        for fund in list_funds():
            state = statuses.get(fund["code"], {})
            rows.append({
                "基金代码": fund["code"], "基金名称": fund["name"],
                "状态": state.get("status", "未更新"),
                "数据来源": state.get("source", fund.get("data_source", "xalpha")),
                "更新时间": state.get("updated_at", ""),
                "说明": state.get("message", ""),
            })
        self._fill_tree(self.tab_data._tree, pd.DataFrame(rows))

    # 基金池表单同时维护基础信息和策略建议所需的定投配置。
    def open_fund_dialog(self, existing=None):
        dialog = self._dialog("基金池管理", 620, 720)
        code = tk.StringVar(value=(existing or {}).get("code", ""))
        name = tk.StringVar(value=(existing or {}).get("name", ""))
        category = tk.StringVar(value=(existing or {}).get("category", "未分类"))
        fund_type = tk.StringVar(value=(existing or {}).get("fund_type", "normal"))
        enabled = tk.BooleanVar(value=(existing or {}).get("enabled", True))
        is_dca = tk.BooleanVar(value=(existing or {}).get("is_dca", False))
        dca_frequency = tk.StringVar(value=(existing or {}).get("dca_frequency", "monthly"))
        dca_amount = tk.StringVar(value=str((existing or {}).get("dca_base_amount", 0)))
        allow_pause = tk.BooleanVar(value=(existing or {}).get("dca_allow_pause", True))
        allow_increase = tk.BooleanVar(value=(existing or {}).get("dca_allow_increase", True))
        max_multiplier = tk.StringVar(value=str((existing or {}).get("dca_max_multiplier", 2)))
        dca_note = tk.StringVar(value=(existing or {}).get("dca_note", ""))
        fields = [
            ("基金代码", ttk.Entry(
                dialog, textvariable=code, width=36,
                state="readonly" if existing else "normal",
            )),
            ("基金名称", ttk.Entry(dialog, textvariable=name, width=36)),
            ("资产类别", ttk.Entry(dialog, textvariable=category, width=36)),
            ("基金类型", ttk.Combobox(dialog, textvariable=fund_type,
                                      values=["normal", "money"], state="readonly", width=34)),
            ("启用", ttk.Checkbutton(dialog, variable=enabled)),
            ("设为定投基金", ttk.Checkbutton(dialog, variable=is_dca)),
            ("定投频率", ttk.Combobox(dialog, textvariable=dca_frequency,
                                      values=["daily", "weekly", "monthly"], state="readonly", width=34)),
            ("基础金额", ttk.Entry(dialog, textvariable=dca_amount, width=36)),
            ("允许暂停", ttk.Checkbutton(dialog, variable=allow_pause)),
            ("允许增强", ttk.Checkbutton(dialog, variable=allow_increase)),
            ("最大倍数", ttk.Entry(dialog, textvariable=max_multiplier, width=36)),
            ("定投备注", ttk.Entry(dialog, textvariable=dca_note, width=36)),
        ]
        for row, (label, widget) in enumerate(fields):
            self._form_row(dialog, row, label, widget)

        def identify():
            try:
                info = self.data_provider.get_fund_info(code.get().strip())
                name.set(info.name)
                messagebox.showinfo("识别成功", f"基金名称：{info.name}", parent=dialog)
            except (ValueError, DataProviderError) as exc:
                messagebox.showerror("识别失败", str(exc), parent=dialog)

        def save():
            try:
                upsert_fund(
                    code.get(), name.get(), category.get(), fund_type.get(), enabled.get(),
                    is_dca=is_dca.get(), dca_frequency=dca_frequency.get(),
                    dca_base_amount=float(dca_amount.get() or 0),
                    dca_allow_pause=allow_pause.get(), dca_allow_increase=allow_increase.get(),
                    dca_max_multiplier=float(max_multiplier.get() or 1), dca_note=dca_note.get(),
                )
                self._load_fund_pool_table()
                self._load_data_center()
                dialog.destroy()
            except ValueError as exc:
                messagebox.showerror("输入错误", str(exc), parent=dialog)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, pady=22)
        ttk.Button(buttons, text="联网识别名称", command=identify).pack(side="left", padx=5)
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=save).pack(side="left", padx=5)

    def _selected_fund(self):
        selected = self.tab_funds._tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一只基金。")
            return None
        code = self.tab_funds._tree.item(selected[0], "values")[0]
        return get_fund(code=code)

    def edit_selected_fund(self):
        fund = self._selected_fund()
        if fund:
            self.open_fund_dialog(fund)

    def toggle_selected_fund(self):
        fund = self._selected_fund()
        if fund:
            set_fund_enabled(fund["code"], not fund.get("enabled", True))
            self._load_fund_pool_table()

    def delete_selected_fund(self):
        fund = self._selected_fund()
        if not fund:
            return
        holding_count = sum(
            h.get("code") == fund["code"] for h in self.store_data["holdings"]
        )
        transaction_count = sum(
            tx.get("fund_code") == fund["code"]
            for tx in self.store_data.get("transactions", [])
        )
        lot_count = sum(
            lot.get("fund_code") == fund["code"]
            for lot in self.store_data.get("lots", [])
        )
        message = (
            f"确认彻底删除 {fund['name']}（{fund['code']}）？\n\n"
            f"同时将删除：\n"
            f"持仓记录 {holding_count} 条\n"
            f"交易流水 {transaction_count} 条\n"
            f"持仓批次 {lot_count} 条\n\n"
            "此操作不可撤销。"
        )
        if messagebox.askyesno("彻底删除基金", message):
            try:
                _, counts = delete_fund_records(fund["code"])
                delete_fund(fund["code"])
                self.refresh_portfolio_data()
                self._load_fund_pool_table()
                self.append_log(
                    f"已删除基金 {fund['name']}，同步清除持仓 {counts['holdings']} 条、"
                    f"交易 {counts['transactions']} 条、批次 {counts['lots']} 条"
                )
            except Exception as exc:
                messagebox.showerror("删除失败", str(exc))

    def open_holding_dialog(self):
        dialog = self._dialog("历史持仓估算录入", 560, 520)
        funds = list_funds(enabled_only=True)
        if not funds:
            messagebox.showerror("无法管理持仓", "基金池中没有启用的基金。")
            return
        labels = [f"{fund['code']} | {fund['name']}" for fund in funds]
        fund_by_label = {label: fund for label, fund in zip(labels, funds)}
        selected_fund = tk.StringVar(value=labels[0])
        category = tk.StringVar()
        amount = tk.StringVar(value="0")
        profit = tk.StringVar(value="0")

        self._form_row(dialog, 0, "基金", ttk.Combobox(
            dialog, textvariable=selected_fund, values=labels, state="readonly", width=42))
        self._form_row(dialog, 1, "资产类别", ttk.Entry(dialog, textvariable=category, width=36))
        self._form_row(dialog, 2, "历史估算市值", ttk.Entry(dialog, textvariable=amount, width=36))
        self._form_row(dialog, 3, "历史估算收益", ttk.Entry(dialog, textvariable=profit, width=36))
        ttk.Label(
            dialog,
            text="此入口仅用于缺少真实份额的历史持仓。保存后将标记为“待估值”，"
                 "精确持仓请使用交易流水或持仓批次。",
            foreground=COLORS["amber"], wraplength=480, justify="left",
        ).grid(row=4, column=0, columnspan=2, padx=24, pady=(8, 4))

        def load_selected(*_args):
            fund = fund_by_label[selected_fund.get()]
            item = next(
                (row for row in self.store_data["holdings"]
                 if row.get("code") == fund["code"]),
                None,
            )
            if item:
                category.set(item["category"])
                amount.set(str(item.get("manual_amount", item.get("amount", 0))))
                profit.set(str(item.get("manual_profit", item.get("profit", 0))))
            else:
                category.set(fund.get("category", "未分类"))
                amount.set("0")
                profit.set("0")

        selected_fund.trace_add("write", load_selected)
        load_selected()

        buttons = ttk.Frame(dialog, padding=(20, 18))
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")

        def save():
            try:
                fund = fund_by_label[selected_fund.get()]
                upsert_holding(fund["code"], category.get().strip(),
                               float(amount.get()), float(profit.get()))
                self.refresh_portfolio_data()
                self.append_log(f"已录入历史估算持仓：{fund['name']}（{fund['code']}）")
                self.set_status("历史持仓待估值，请重新分析", COLORS["amber"])
                dialog.destroy()
            except ValueError:
                messagebox.showerror("输入错误", "持仓金额和收益必须是有效数字", parent=dialog)

        def remove():
            if messagebox.askyesno("删除持仓", "确认删除这项持仓？历史交易流水不会删除。",
                                   parent=dialog):
                fund = fund_by_label[selected_fund.get()]
                delete_holding(fund["name"])
                self.refresh_portfolio_data()
                dialog.destroy()

        ttk.Button(buttons, text="删除持仓", command=remove).pack(side="left", padx=5)
        ttk.Button(buttons, text="保存修改", style="Accent.TButton", command=save).pack(side="left", padx=5)

    # 交易登记保存确认数据，并通过批次模型同步重建持仓。
    def open_transaction_dialog(self):
        dialog = self._dialog("登记确认交易", 560, 590)
        funds = list_funds(enabled_only=True)
        if not funds:
            messagebox.showerror("无法登记交易", "基金池中没有启用的基金。")
            return
        labels = [f"{item['code']} | {item['name']}" for item in funds]
        fund_by_label = {label: item for label, item in zip(labels, funds)}
        fund = tk.StringVar(value=labels[0])
        action = tk.StringVar(value="买入")
        confirm_date = tk.StringVar(value=datetime.date.today().isoformat())
        amount = tk.StringVar()
        shares = tk.StringVar()
        nav = tk.StringVar()
        fee = tk.StringVar(value="0")
        note = tk.StringVar()

        fields = [
            ("基金", ttk.Combobox(dialog, textvariable=fund, values=labels, state="readonly", width=42)),
            ("交易类型", ttk.Combobox(dialog, textvariable=action, values=["买入", "卖出"],
                                      state="readonly", width=34)),
            ("确认日期", ttk.Entry(dialog, textvariable=confirm_date, width=36)),
            ("确认金额", ttk.Entry(dialog, textvariable=amount, width=36)),
            ("确认份额（可选）", ttk.Entry(dialog, textvariable=shares, width=36)),
            ("确认净值（可选）", ttk.Entry(dialog, textvariable=nav, width=36)),
            ("手续费", ttk.Entry(dialog, textvariable=fee, width=36)),
            ("备注", ttk.Entry(dialog, textvariable=note, width=36)),
        ]
        for row, (label, widget) in enumerate(fields):
            self._form_row(dialog, row, label, widget)

        hint = ttk.Label(dialog, text="确认份额必填；也可填写确认净值，由系统自动计算份额。",
                         foreground=COLORS["muted"])
        hint.grid(row=8, column=0, columnspan=2, padx=20, pady=(10, 0))

        def save():
            try:
                add_transaction(
                    fund_by_label[fund.get()]["code"], action.get(), confirm_date.get().strip(),
                    float(amount.get()), float(shares.get() or 0), float(nav.get() or 0),
                    float(fee.get() or 0), note.get(),
                )
                self.refresh_portfolio_data()
                selected = fund_by_label[fund.get()]
                self.append_log(
                    f"已登记{action.get()}：{selected['name']}（{selected['code']}），"
                    f"确认金额 ¥{float(amount.get()):,.2f}"
                )
                self.set_status("交易已登记，请重新分析", COLORS["amber"])
                dialog.destroy()
            except ValueError as exc:
                messagebox.showerror("输入错误", str(exc), parent=dialog)

        ttk.Button(dialog, text="确认并同步持仓", style="Accent.TButton",
                   command=save).grid(row=9, column=0, columnspan=2, pady=22)

    def delete_selected_transaction(self):
        tree = self.tab_transactions._tree
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在交易流水中选择一条记录。")
            return
        transaction_id = tree.item(selected[0], "values")[0]
        if messagebox.askyesno("撤销交易", "撤销后将反向调整当前持仓，是否继续？"):
            try:
                delete_transaction(transaction_id)
                self.refresh_portfolio_data()
                self.append_log("已撤销交易并同步持仓")
                self.set_status("交易已撤销，请重新分析", COLORS["amber"])
            except ValueError as exc:
                messagebox.showerror("无法撤销", str(exc))

    def _dialog(self, title, width, height):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry(f"{width}x{height}")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)
        return dialog

    @staticmethod
    def _form_row(parent, row, label, widget):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e",
                                           padx=(24, 12), pady=12)
        widget.grid(row=row, column=1, sticky="w", padx=(0, 24), pady=12)

    def _read_output_csv(self, filename):
        path = os.path.join(OUTPUT_DIR, filename)
        return pd.read_csv(path, encoding="utf-8-sig") if os.path.exists(path) else pd.DataFrame()

    @staticmethod
    def _money_value(value):
        return float(str(value).replace("CNY", "").replace(",", "").strip())

    @staticmethod
    def _dca_dataframe_to_results(df):
        results = {}
        period_map = {"半年(180天)": 180, "一年(365天)": 365, "两年(730天)": 730}
        for _, row in df.iterrows():
            days = period_map.get(row.get("模拟周期"))
            if days is None:
                continue
            results.setdefault(row["基金名称"], {})[days] = {
                "累计投入": row["累计投入"],
                "市值": row["当前市值"],
                "收益率": row["收益率"],
            }
        return results

    # 日志控件仅在主线程追加文本，并保持只读状态。
    def append_log(self, message):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        for line in str(message).splitlines() or [""]:
            self.log_text.insert("end", f"[{now}]  {line}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # 顶部状态同时更新文字和颜色点，不参与业务状态计算。
    def set_status(self, text, color):
        self.status_label.configure(text=text)
        self.status_dot.configure(fg=color)

    def open_output_folder(self):
        if os.path.isdir(OUTPUT_DIR):
            os.startfile(OUTPUT_DIR)

    def open_report(self):
        path = os.path.join(PROJECT_DIR, "portfolio_report.md")
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo("提示", "报告尚未生成，请先运行分析。")

    def _on_close(self):
        self.analysis_running = False
        plt.close("all")
        self.root.destroy()


def main():
    enable_windows_dpi_awareness()
    root = tk.Tk()
    configure_tk_dpi(root)
    PortfolioAnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
