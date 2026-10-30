"""Pydantic validation schemas.

Stage 00 placeholder. Domain schemas will be added in later stages.
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthCheck(BaseModel):
    status: str = "ok"