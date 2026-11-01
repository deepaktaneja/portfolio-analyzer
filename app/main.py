"""Streamlit entry point."""
from __future__ import annotations
import os, runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
os.chdir(ROOT)
runpy.run_path(str(ROOT/"app.py"), run_name="__main__")
