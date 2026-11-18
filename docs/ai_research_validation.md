# AI Research Quality Validation — Stage 11

**Scope:** audit of MCP evidence accuracy/freshness/citation, LLM research for hallucination and
unsupported claims, quarterly/analyst/governance/forward-return handling, and cross-run consistency.

**Method:** static trace of the research pipeline (`research/orchestrator.py`, `research/llm.py`,
`research/forecast.py`, `decision/`), evidence-envelope inspection, and existing tests
(`test_research_orchestrator.py`, `test_portfolio_research.py`, `test_decision_scenarios_tax.py`,
`test_stage10_*`). Live MCP/LLM calls are **not** executed in the audit (no external calls without
approval); structural guarantees and offline degradation are validated instead.

**Result summary:** evidence citation, tiering, prompt-injection sanitisation, and LLM/accounting
separation are **sound**. Two limitations are documented (not auto-"fixed"): no automated
claim-vs-source verification, and non-deterministic LLM text across runs. No fabricated data.

---

## 1. MCP data accuracy, freshness & citations

| Control | Implementation | Verdict |
|---|---|---|
| Every MCP call wrapped in an evidence envelope | `mcp_integration/adapter.build_envelope` (server/tool/args/retrieved_at/evidence_id) | ✅ |
| Cited facts traceable | `ResearchContext.evidence_ids` collected and stored on the report | ✅ |
| Cache idempotency & TTL by capability | `MCPCacheStore` (args_hash; TTL 1h prices … 7d history) | ✅ |
| Freshness surfaced | `retrieved_at`/`timestamps` embedded into the prompt; stale-cache flagged `partial` | ✅ |
| Oversized/injected responses | `mcp_integration/validation.validate_response` → `partial`, not cached | ✅ (Stage 10) |
| Failure degradation | error/partial envelope; `ctx.errors` listed as **DATA GAPS** in the prompt | ✅ |

Evidence is never invented: absent data appears as an explicit data gap, not a fabricated metric.

---

## 2. Hallucination & unsupported-forecast controls

| Control | Implementation | Verdict |
|---|---|---|
| Three information tiers | facts / analyst-consensus / AI-forecast buckets, `tier` on signals | ✅ |
| Citation discipline in prompt | prompts require citing source tool + timestamp per factual claim | ✅ (structural) |
| AI forecasts labelled | prompt mandates `AI FORECAST (not analyst consensus)` | ✅ (structural) |
| LLM cannot alter accounting | LLM only in `research/` & `decision/`; outputs are text/signals; accounting is pure Python | ✅ (dependency-enforced) |
| Scenario separation | `decision/scenarios` tags `data_tier="ai_forecast"`, explicit weights | ✅ |
| Prompt-injection defence | `sanitise_mcp_content` + `sanitise_external_text` strip injection phrases | ✅ `test_prompt_injection_neutralised` |

### AI-01 — No automated verification that each claim maps to a cited source — **Medium (DOCUMENTED)**
- **Limitation:** prompts strongly encourage citations and label AI forecasts, but the pipeline does
  not programmatically verify that every numeric claim has a corresponding `evidence_id`.
- **Mitigation today:** evidence IDs stored with the report; forecasts labelled; data gaps listed; LLM
  has no accounting authority.
- **Recommended (future):** post-generation citation checker requiring each numeric claim to cite an
  evidence tool/timestamp; flag/reject uncited claims. (New feature → deferred.)

### AI-02 — Cross-run consistency is not guaranteed — **Low (DOCUMENTED)**
- **Observation:** temperature `0.1` and deterministic prompts, but provider sampling can vary wording;
  MCP results are cached, **LLM text is not**. Sourced facts are stable (from evidence); phrasing may differ.

---

## 3. Quarterly results, analyst estimates, governance, forward scenarios

| Domain | Source path | Validation |
|---|---|---|
| Quarterly results | `TOOL_DOMAINS["quarterly"] → get_quarterly_results` | Included with timestamp; absence → data gap. |
| Analyst estimates | `TOOL_DOMAINS["analyst"] → get_analyst_targets` | Bucketed as `analyst_consensus`; distinct from AI forecast. |
| Governance | `get_shareholding_pattern`, `analyze_red_flags`, `get_corporate_actions` | Feeds `S_GOVERNANCE`; CRITICAL governance escalates independently. |
| Forward-return scenarios | `decision/scenarios.forward_scenarios` → `research/forecast.ForecastEngine` | Bear/base/bull + probability weighting; tagged AI forecast. |
| Portfolio-impact hand-off | `portfolio_context` passed as internal accounting (explicitly not LLM-computable) | ✅ separation upheld |

Existing tests: `test_research_orchestrator` (evidence collection/errors), `test_decision_scenarios_tax`
(scenario weighting/upside-downside + tax separation).

---

## 4. LLM usage, cost & privacy

- Token/cost/latency tracked per call (`research/usage.UsageTracker`) as counts + estimated cost;
  **prompt/response text is not stored** (privacy). `LLMClient.usage_tracker` is opt-in.
- Secrets/PII redacted from logs (`security/redaction`); external text sanitised before prompts.
- Deterministic accounting is independent of the LLM (dependency direction enforced).

---

## 5. Sign-off

- Evidence citation, tiering, injection defence, and LLM/accounting separation: **verified**.
- Two documented limitations (AI-01 automated claim verification; AI-02 cross-run text variance);
  both require new capability/feature work → deferred per the "no new features" constraint.
- No fabricated research data introduced in Stage 11.

- **Recommended:** store a prompt hash and optionally cache generated reports per (security, evidence-set).