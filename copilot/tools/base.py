"""Tool specification + in-process registry for the copilot.

Every tool is a pure, read-only function ``run(args, ctx) -> ToolResult`` that wraps an
existing deterministic engine. Tools declare an ``args_schema`` used by the security
validator. The registry enforces an allowlist so the LLM can never invoke arbitrary code.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from copilot.context import CopilotContext
from copilot.types import TIER_FACT, ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    keywords: tuple[str, ...]
    run: Callable[[dict, CopilotContext], ToolResult]
    args_schema: dict = field(default_factory=dict)
    tier: str = TIER_FACT


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate tool '{spec.name}'")
    _REGISTRY[spec.name] = spec
    return spec


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def list_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def enabled_tools(enabled: list[str] | None = None) -> list[ToolSpec]:
    specs = list_tools()
    if enabled is None:
        return specs
    return [s for s in specs if s.name in set(enabled)]


def clear_registry() -> None:
    """Test helper — reset the registry."""
    _REGISTRY.clear()
