"""Streamlit entry point for the Indian equity portfolio analyzer."""

from __future__ import annotations

import streamlit as st


def main() -> None:
    st.set_page_config(page_title="Indian Portfolio Analyzer", layout="wide")
    st.title("Indian Equity Portfolio Analyzer")
    st.caption("Stage 00: project scaffolding is initialized.")


if __name__ == "__main__":
    main()