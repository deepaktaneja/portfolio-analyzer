"""Application settings loaded from .env and YAML/JSON registry.

Pydantic v2 settings. All financial calculation defaults are deterministic.
LLM is used for research/interpretation only — never for accounting.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"

# Load .env if present (never fail if missing)
load_dotenv(BASE_DIR / ".env", override=False)


class MCPServerConfig(BaseSettings):
    """Single MCP server entry in the app's own registry."""

    name: str
    transport: str  # "stdio" | "streamable_http"
    command: str | None = None  # for stdio
    args: list[str] | None = None  # for stdio
    url: str | None = None  # for streamable_http
    enabled: bool = True
    max_concurrency: int = 2
    timeout_seconds: int = 30


class Settings(BaseSettings):
    """Global application settings."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database paths
    xirr_db_path: str = Field(default="sqlite:///data/xirr.sqlite3")
    market_data_db_path: str = Field(default="sqlite:///data/market_data.sqlite3")

    # Portfolio folders
    portfolio_folder: str = Field(default="data/portfolio")
    statement_folder: str = Field(default="data/statements")

    # LLM provider chain (Gemini primary, OpenRouter fallback)
    gemini_api_key: str | None = Field(default=None)
    openrouter_api_key: str | None = Field(default=None)
    llm_temperature: float = Field(default=0.1)
    llm_max_tokens: int = Field(default=4000)

    # MCP cache TTL by capability class (seconds)
    cache_ttl_historical_prices: int = Field(default=7 * 24 * 3600)  # 7 days
    cache_ttl_quarterly_financials: int = Field(default=1 * 24 * 3600)  # 1 day
    cache_ttl_current_prices: int = Field(default=60 * 60)  # 1 hour

    # Circuit breaker
    circuit_breaker_failures: int = Field(default=3)
    circuit_breaker_cooldown_seconds: int = Field(default=300)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()


def load_mcp_registry(path: str | os.PathLike | None = None) -> list[MCPServerConfig]:
    """Load the app's own MCP server registry from JSON.

    Does not import from or reference /Users/anmol/stock-selection-agent.
    """
    import json

    registry_path = Path(path) if path else CONFIG_DIR / "mcp_servers.json"
    if not registry_path.exists():
        return []
    with registry_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    servers: list[MCPServerConfig] = []
    for entry in raw.get("servers", []):
        servers.append(MCPServerConfig(**entry))
    return servers