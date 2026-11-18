# Copilot Architecture — Stage 12

The **AI Investment Copilot** answers natural-language questions about the portfolio. It is
**tool-first**: every number is produced by an existing deterministic engine; the LLM only routes
intent and phrases an explanation around those figures. It never computes or invents portfolio
values and never executes trades.

## 1. Data flow

```
User question
   │  sanitise (copilot.security.sanitise_user_input)         ← prompt-injection defence
   ▼
IntentRouter (copilot.intent.classify)                        ← deterministic keyword+arg parsing
   │  (optional) LLM structured {tool,args} fallback, validated against the allowlist
   ▼
ToolRegistry (copilot.tools)                                  ← allowlisted, read-only tools
   │  validate_args (schema, types, ranges)                   ← least privilege
   ▼
Deterministic engines (performance.xirr, portfolio.*, mf.backtesting, decision.*, research.*)
   ▼
ToolResult (facts + citations + warnings + tier)              ← single source of truth for numbers
   ▼
ResponseBuilder (copilot.response)                            ← markdown + sources + uncertainty + tier
   │  (optional) LLM narration with "use ONLY these numbers"  ← phrased explanation, no computation
   ▼
CopilotResponse → UI chat panel (copilot.ui)
```

## 2. Modules

| Module | Responsibility |
|---|---|
| `copilot/config.py` | Loads `config/copilot.yaml` over safe defaults |
| `copilot/types.py` | `Citation`, `ToolResult`, `CopilotResponse`, tier constants |
| `copilot/security.py` | Input sanitisation, injection detection, arg validation, rate limiting, audit |
| `copilot/context.py` | Read-only `CopilotContext` snapshot (equity from holdings, MF best-effort) |
| `copilot/tools/base.py` | `ToolSpec` + registry + allowlist |
| `copilot/tools/{portfolio,rebalancing,backtesting,signals,research}.py` | The 14 deterministic tools |
| `copilot/intent.py` | Deterministic routing + amount/percent/security extraction + LLM fallback |
| `copilot/response.py` | Answer composition (tables, sources, warnings, tier badge) + narration |
| `copilot/engine.py` | Orchestration: sanitise → route → validate → run → answer |
| `copilot/ui.py` | Streamlit "Ask My Portfolio" panel (inside existing pages) |

## 3. Tool-first guarantee (no LLM arithmetic)

- The LLM is **never** given raw numeric reasoning authority. Tools return a `ToolResult` whose
  `data` is the authoritative figure set.
- Narration prompt: *"Use ONLY the numbers in FACTS. Do not compute, extrapolate or invent any figure."*
- If the LLM is unavailable (no keys), the copilot still returns complete, correct deterministic
  answers (narration is simply skipped).
- Numeric outputs are the engine outputs; tests assert equality with the engines (see
  `tests/unit/test_copilot_tools.py`).

## 4. Security & trust boundaries

- **Input sanitation** strips prompt-injection phrases and truncates to `security.max_input_chars`.
- **Allowlist**: only registered tools can run; the LLM cannot call arbitrary code/data.
- **Argument validation**: JSON-schema-like checks (types, required, min/max); unknown args dropped.
- **Authorization**: all tools are read-only (`view` permission); no trade tool exists.
- **Audit**: every tool call recorded (tool, redacted args, ok, summary). Free-text args are masked in
  logs; portfolio identifiers never appear in log messages.
- **Privacy**: conversation history is session-only (`st.session_state`), capped at `history.max_turns`,
  and clearable; nothing is persisted to disk and prompt/response text is not stored by the usage tracker.
- **Read-only data access**: tools only read the app's own SQLite/`AppContext` and (optionally) MCP via
  the existing research orchestrator; no writes, no trade execution.

## 5. LLM & MCP efficiency

- Research is **cached-first**: a fresh stored report (≤7 days) is returned without any MCP/LLM call.
- Live research is only generated when a provider is configured; otherwise the copilot explicitly
  reports "research unavailable" (no fabrication).
- MCP evidence is wrapped by the existing adapter (TTL cache, circuit breaker, size guard).
- Token/cost usage is recorded by `research/usage.UsageTracker` when configured.

## 6. UI integration (no new top-level tabs)

The panel renders as an expander **inside** the existing five pages:
`Portfolio`, `Performance`, `Holdings & Research`, `Backtesting` (and is available from the shared
component). Each page passes its `AppContext`; the copilot builds a context snapshot only when a
question is submitted (suggestions are static), so page render stays fast. Answers include a
collapsible **"Supporting calculations & sources"** section (raw tool data, citations, warnings).
