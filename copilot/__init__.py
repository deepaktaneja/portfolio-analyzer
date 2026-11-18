"""Stage 12 — AI Investment Copilot (conversational portfolio intelligence).

The copilot is deliberately **tool-first**: every figure in an answer is produced by a
deterministic engine (XIRR, allocation, rebalancing, backtesting, signals, research).
The LLM is used only to route intent and to phrase explanations around those figures —
it never computes or invents portfolio numbers.
"""
from copilot.engine import CopilotEngine, answer
from copilot.types import Citation, CopilotResponse, ToolResult

__all__ = ["Citation", "CopilotEngine", "CopilotResponse", "ToolResult", "answer"]
