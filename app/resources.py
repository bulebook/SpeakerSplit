"""资源路径：开发时取项目目录，打包后取 PyInstaller 解包目录。"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def icon_ico() -> Path:
    return resource_path("assets/app.ico")


def icon_png() -> Path:
    return resource_path("assets/icon.png")
