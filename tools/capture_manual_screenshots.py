from __future__ import annotations

import time
from pathlib import Path
import sys

from PIL import ImageGrab
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ui_app import PortfolioAnalyzerApp


OUTPUT = ROOT / "docs" / "screenshots"

TARGETS = [
    ("01_总览与图表", "总览与图表"),
    ("02_持仓明细", "持仓明细"),
    ("03_持仓批次", "持仓批次"),
    ("04_基金池管理", "基金池管理"),
    ("05_数据中心", "数据中心"),
    ("06_策略回测", "策略回测"),
    ("07_策略建议", "策略建议"),
]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.geometry("1600x900+20+20")
    app = PortfolioAnalyzerApp(root)
    root.update()
    time.sleep(1)

    tabs = app.notebook.tabs()
    labels = {
        "".join(app.notebook.tab(tab_id, "text").split()): tab_id
        for tab_id in tabs
    }
    print(f"tabs={list(labels)}")
    for filename, label in TARGETS:
        normalized = "".join(label.split())
        tab_id = next(
            (tab for text, tab in labels.items() if normalized in text or text in normalized),
            None,
        )
        if not tab_id:
            print(f"skip={label}")
            continue
        app.notebook.select(tab_id)
        root.update_idletasks()
        root.update()
        time.sleep(0.35)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        path = OUTPUT / f"{filename}.png"
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(path)
        print(path)

    root.destroy()


if __name__ == "__main__":
    main()
