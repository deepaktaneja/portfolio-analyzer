"""LLM client for research orchestration.

Fallback chain: Gemini (primary) -> OpenRouter (fallback).
Low temperature for deterministic research. LLM outputs are research /
interpretation only — never accounting.

Prompt injection safety: MCP content is treated as untrusted and is
sanitised before inclusion in prompts. The LLM is never allowed to alter
accounting figures.
"""
from __future__ import annotations

import re

import httpx

from config.settings import get_settings

# Characters/patterns that indicate an attempt to hijack the LLM via MCP content.
_PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"system\s*:",
    r"you\s+are\s+now",
    r"disregard\s+",
    r"\\n\\nHuman:",
    r"\\n\\nAssistant:",
]


def sanitise_mcp_content(content: str) -> str:
    """Strip potential prompt-injection patterns from MCP-derived text before it enters a prompt."""
    cleaned = content
    for pat in _PROMPT_INJECTION_PATTERNS:
        cleaned = re.sub(pat, " [redacted] ", cleaned, flags=re.IGNORECASE)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class LLMResult:
    """Structured result from an LLM call."""

    def __init__(self, text: str, model: str, provider: str, raw: dict | None = None):
        self.text = text
        self.model = model
        self.provider = provider
        self.raw = raw or {}

    def __repr__(self) -> str:
        return f"LLMResult(provider={self.provider}, model={self.model}, len={len(self.text)})"


class LLMClient:
    """LLM client with provider fallback chain.

    Tries Gemini first, then OpenRouter. Each provider is attempted once;
    on failure or missing credentials, falls back to the next.
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._gemini_client = None
        self._http_client: httpx.Client | None = None
        self.usage_tracker = None  # optional research.usage.UsageTracker

    @property
    def http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=30.0)
        return self._http_client

    def _gemini_available(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def _openrouter_available(self) -> bool:
        return bool(self.settings.openrouter_api_key)

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> LLMResult | None:
        if not self._gemini_available():
            return None
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            return None
        try:
            genai.configure(api_key=self.settings.gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            resp = model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=self.settings.llm_temperature,
                    max_output_tokens=self.settings.llm_max_tokens,
                ),
            )
            text = getattr(resp, "text", "") or ""
            return LLMResult(text=text, model="gemini-1.5-flash", provider="gemini")
        except Exception:  # noqa: BLE001
            return None

    def _call_openrouter(self, system_prompt: str, user_prompt: str) -> LLMResult | None:
        if not self._openrouter_available():
            return None
        try:
            resp = self.http_client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openrouter/auto",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": self.settings.llm_temperature,
                    "max_tokens": self.settings.llm_max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return None
            text = choices[0].get("message", {}).get("content", "") or ""
            model = data.get("model", "openrouter/auto")
            return LLMResult(text=text, model=model, provider="openrouter")
        except Exception:  # noqa: BLE001
            return None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMResult:
        """Generate text using the LLM fallback chain.

        Returns the first successful result. If all providers fail,
        returns an LLMResult with empty text and provider="none".
        Records token/cost usage when a ``usage_tracker`` is configured.
        """
        import time

        start = time.monotonic()
        # Sanitise any MCP-derived content that may be embedded in prompts
        system_prompt = sanitise_mcp_content(system_prompt)
        user_prompt = sanitise_mcp_content(user_prompt)

        final: LLMResult | None = None
        result = self._call_gemini(system_prompt, user_prompt)
        if result is not None and result.text.strip():
            final = result
        else:
            result = self._call_openrouter(system_prompt, user_prompt)
            if result is not None and result.text.strip():
                final = result

        if final is None:
            final = LLMResult(
                text="",
                model="none",
                provider="none",
                raw={"error": "all LLM providers unavailable"},
            )

        self._record_usage(system_prompt, user_prompt, final, time.monotonic() - start)
        return final

    def _record_usage(self, system_prompt: str, user_prompt: str, result: LLMResult, elapsed: float) -> None:
        """Persist token/cost usage (best-effort; never affects the returned result)."""
        if self.usage_tracker is None:
            return
        try:
            from research.usage import estimate_tokens

            self.usage_tracker.record(
                provider=result.provider,
                model=result.model,
                prompt_tokens=estimate_tokens(system_prompt) + estimate_tokens(user_prompt),
                completion_tokens=estimate_tokens(result.text),
                elapsed_seconds=round(float(elapsed), 4),
                metadata={"available": result.provider != "none"},
            )
        except Exception:  # noqa: BLE001
            return