"""Copilot tool registry. Importing this package registers all built-in tools."""
from __future__ import annotations

from copilot.tools import backtesting as _backtesting  # noqa: F401

# Importing the modules registers their ToolSpecs (side effect).
from copilot.tools import portfolio as _portfolio  # noqa: F401
from copilot.tools import rebalancing as _rebalancing  # noqa: F401
from copilot.tools import research as _research  # noqa: F401
from copilot.tools import signals as _signals  # noqa: F401
from copilot.tools.base import (
    ToolSpec,
    clear_registry,
    enabled_tools,
    get_tool,
    list_tools,
    register,
)

__all__ = ["ToolSpec", "clear_registry", "enabled_tools", "get_tool", "list_tools", "register"]
