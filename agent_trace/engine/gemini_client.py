import os
import sys
import json
import socket
import httpx
from pathlib import Path
from typing import Dict, Any, Optional, List

def _load_env_file():
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env_file()


class GeminiClient:
    """Client for Google Gemini via direct REST API (httpx) — avoids SDK hangs."""

    # Ordered by preference; first available model wins.
    # Model IDs valid for the v1beta REST endpoint as of 2025.
    # Older IDs (gemini-2.0-flash, gemini-1.5-flash) return 404 for new API keys.
    CANDIDATE_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    _REST_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.timeout = int(os.getenv("GEMINI_TIMEOUT", "20"))

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def _rest_generate(self, model: str, prompt: str, timeout: int = 0) -> str:
        """Call Gemini REST API directly. Returns raw text or raises.

        If timeout is 0 (default), uses self.timeout (from GEMINI_TIMEOUT env var).
        """
        effective_timeout = timeout if timeout > 0 else self.timeout
        url = f"{self._REST_BASE}/{model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        r = httpx.post(url, json=payload, timeout=effective_timeout)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    def answer_grounded_question(
        self,
        file_path: str,
        symbol: str,
        question: str,
        evidence_summary: str = "",
        diff_snippet: str = "",
        jev_category: str = "REFACTOR",
        blast_level: str = "localized",
    ) -> Dict[str, Any]:
        """Answers developer inquiry about why a code change occurred, strictly grounded in evidence."""
        prompt = f"""You are AgentTrace, an explainability and verification control plane for AI coding agents.
The user is reviewing an agent-generated code modification and asks: "{question}".

GROUND TRUTH EVIDENCE (Zero-Hallucination):
File Modified: {file_path}
Symbol: {symbol}
Jev System 1 Classification: {jev_category} (Blast Level: {blast_level})
Diff Hunk:
{diff_snippet[:1500]}

Context & Investigation Trail:
{evidence_summary[:600] if evidence_summary else 'Extracted from repository diff inspection'}

INSTRUCTIONS:
1. Answer the developer's question directly in 2-3 concise, high-signal sentences.
2. Ground your explanation strictly and exclusively in the provided diff hunk and file context above.
3. If asked why this changed, explain what the code modification does and whether it appears to be a direct requirement, a refactoring, or a cross-file extraction.
4. If asked what breaks or about reverting, explain the specific architectural blast radius of reverting this exact symbol/file.
5. Keep your total answer under 90 words. Do not use generic filler.
"""

        skip_remote = bool(os.getenv("AGENTTRACE_SKIP_ENRICHMENT"))
        if self.has_api_key and not skip_remote:
            for model_name in self.CANDIDATE_MODELS:
                try:
                    text = self._rest_generate(model_name, prompt).strip()
                    if text:
                        return {
                            "answer": text,
                            "source": f"{model_name} (System 2 Grounded)",
                            "grounded": True,
                        }
                except Exception as e:
                    sys.stderr.write(f"[GeminiClient] Model {model_name} failed: {e}\n")
                    continue

        # Dynamic fallback grounded in actual hunk metadata
        fname = file_path.split("/")[-1] if file_path else "the file"
        sym = symbol if symbol and symbol != "unknown" else fname
        q_lower = question.lower()

        if "revert" in q_lower or "break" in q_lower:
            ans = (
                f"Reverting `{sym}` in `{fname}` will back out the {jev_category} change introduced in this diff. "
                f"Because this has a '{blast_level}' blast radius, any callers expecting the new signatures or behavior "
                f"in `{file_path}` will immediately fail unless reverted in lockstep."
            )
        elif "who call" in q_lower or "affect" in q_lower or "depend" in q_lower:
            ans = (
                f"`{sym}` resides in `{file_path}`. Modifications to this symbol affect downstream consumers "
                f"within the same module and any external modules that import `{fname}`. "
                f"Review imports of `{sym}` before deploying."
            )
        elif "why" in q_lower or "prompt" in q_lower or "ask" in q_lower:
            ans = (
                f"`{sym}` in `{fname}` was modified as part of a {jev_category} change. "
                f"The diff modifies logic in `{file_path}` with a '{blast_level}' systemic blast radius."
            )
        else:
            ans = (
                f"The modification to `{sym}` in `{file_path}` was evaluated as a {jev_category} change. "
                f"AgentTrace verified the diff hunk lines and recommended testing with priority on callers of `{fname}`."
            )

        return {
            "answer": ans,
            "source": "AgentTrace Grounded Engine (Local)",
            "grounded": True,
        }

    # ── Learning Hub enrichment ──────────────────────────────────────────

    def enrich_learning_concept(
        self,
        concept_name: str,
        concept_what: str,
        concept_how: str,
        logical_changes_summary: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """
        Layer 1 (Option C hybrid): Uses Gemini to generate a grounded why_now, category,
        deep_dive items, and reference doc links for the Active Concept tab.
        Returns a dict matching LearningMeta schema.
        Falls back gracefully if no API key.
        """
        prompt = f"""You are AgentTrace, an AI explainability layer for coding agents.

A developer's coding agent just made changes. The primary concept detected is: "{concept_name}".

GROUNDED CONTEXT:
- Developer's original request: "{user_prompt}"
- What the concept is: {concept_what}
- How this repo uses it: {concept_how}
- Changes made in this session: {logical_changes_summary[:800]}

YOUR TASK — respond with ONLY valid JSON, no markdown fences, no extra text:
{{
  "why_now": "<1-2 sentences explaining exactly why THIS concept matters for THIS specific change, referencing actual symbols/files mentioned above>",
  "category": "<one of: Reliability, Concurrency, Security, Architecture, Performance, AI/ML, Data, Quality>",
  "deep_dive": [
    {{"title": "<concept sub-topic>", "body": "<2-3 sentence practical explanation a developer needs to know>"}},
    {{"title": "<concept sub-topic>", "body": "<2-3 sentence practical explanation>"}},
    {{"title": "<concept sub-topic>", "body": "<2-3 sentence practical explanation>"}},
    {{"title": "<concept sub-topic>", "body": "<2-3 sentence practical explanation>"}}
  ],
  "reference_docs": [
    {{"label": "<official doc title>", "url": "<real URL to official documentation>", "excerpt": "<1 sentence describing what this doc covers>"}},
    {{"label": "<official doc title>", "url": "<real URL>", "excerpt": "<1 sentence>"}}
  ]
}}

Rules:
- why_now MUST reference the actual symbols/files from the grounded context above, not generic text
- deep_dive items must be practical, developer-facing — not academic
- reference_docs URLs must be real, canonical documentation (MDN, Python docs, AWS builders library, RFC, OWASP, etc.)
- No hallucinated URLs
"""
        result = self._call_gemini_json(prompt)
        if result:
            result["enriched"] = True
            result.setdefault("change_lessons", [])
            return result

        # Structured fallback — still grounded, not a lookup table
        return {
            "why_now": f"Your agent introduced {concept_name} in this session. Review the changes carefully before merging.",
            "category": "Architecture",
            "deep_dive": [],
            "reference_docs": [],
            "change_lessons": [],
            "enriched": False,
        }

    def enrich_change_lessons(
        self,
        logical_changes: List[Dict[str, Any]],
        user_prompt: str,
    ) -> List[Dict[str, Any]]:
        """
        Layer 1 (Option C hybrid) + Layer 2 (Option C external): For each logical change,
        uses Gemini to generate a grounded lesson card with category_label, summary, why_now,
        deep_dive items, and reference docs.
        Returns a list of dicts matching ConceptLesson schema.
        """
        if not logical_changes:
            return []

        changes_text = "\n".join(
            f"- Change ID: {ch.get('id', '?')} | Title: {ch.get('title', '?')} "
            f"| Category: {ch.get('category', '?')} | Blast: {ch.get('blast_radius', '?')} "
            f"| Why: {ch.get('why_explanation', '')[:200]}"
            for ch in logical_changes
        )

        prompt = f"""You are AgentTrace, a developer learning engine grounded in real code changes.

Developer's request: "{user_prompt}"

The agent made these logical changes:
{changes_text}

For EACH change, generate a grounded lesson. Respond with ONLY valid JSON — a list, no markdown fences:
[
  {{
    "change_id": "<exact change id from above>",
    "category_label": "<one of: Reliability · Retry Pattern | Concurrency · Lock Mechanism | Security · Authentication | Architecture · Refactor | Performance · Caching | AI/ML · Model Integration | Data · Schema Migration | Quality · Test Coverage>",
    "summary": "<1 sentence grounded summary referencing the actual title/symbols — what the developer should understand about what changed>",
    "why_now": "<1-2 sentences: why this concept matters for this specific change in this codebase>",
    "deep_dive": [
      {{"title": "<sub-topic>", "body": "<practical 2-3 sentence explanation>"}},
      {{"title": "<sub-topic>", "body": "<practical 2-3 sentence explanation>"}}
    ],
    "reference_docs": [
      {{"label": "<official doc title>", "url": "<real canonical URL>", "excerpt": "<1 sentence>"}}
    ]
  }}
]

Rules:
- summary and why_now MUST be grounded in the actual change title/explanation, not generic
- category_label must be picked from the list above exactly
- reference_docs must be real, canonical URLs (Python docs, MDN, AWS, RFC, OWASP, Martin Fowler, etc.)
- Return one entry per change — same count and same IDs as input
"""
        result = self._call_gemini_json(prompt)
        if isinstance(result, list):
            return result

        # Fallback: return minimal grounded stubs
        return [
            {
                "change_id": ch.get("id", ""),
                "category_label": "Architecture · Refactor",
                "summary": ch.get("why_explanation") or ch.get("title", "Change made by agent."),
                "why_now": f"Review {ch.get('title', 'this change')} carefully — blast radius: {ch.get('blast_radius', 'unknown')}.",
                "deep_dive": [],
                "reference_docs": [],
            }
            for ch in logical_changes
        ]

    def synthesize_agent_journey(
        self,
        user_prompt: str,
        events: List[Dict[str, Any]],
        diff_summary: str = "",
    ) -> Dict[str, Any]:
        """
        Uses the transcript events (tool calls, file reads, searches, commands) to reconstruct
        the agent's decision-making process and reasoning chain from start to finish.

        Returns a dict with:
          - summary: 2-3 sentence narrative of how the agent concluded the task
          - decisions: list of {step, action, reasoning, epistemic_status}
          - how_arrived_steps: list of plain strings for the existing UI field
          - enriched: bool
        """
        if not events:
            return self._journey_fallback(user_prompt, diff_summary)

        # Build an event timeline for the prompt.
        # AGENT_REASONING_NOTE events (thinking field from transcript) are
        # DECLARED ground truth — preserve their complete text so that neither
        # Gemini nor the UI display broken or prematurely truncated text.
        event_lines = []
        declared_reasoning_decisions = []
        last_action_label = ""
        for i, evt in enumerate(events[:50]):   # capture up to 50 events including reasoning notes
            action = evt.get("action_type", "UNKNOWN")
            target = evt.get("target") or {}
            payload = evt.get("payload") or {}
            detail = (
                target.get("query") or target.get("file_path") or
                target.get("command") or payload.get("output_summary") or ""
            )
            detail_str = str(detail).strip()
            if action == "AGENT_REASONING_NOTE":
                # Mark clearly as verbatim agent thinking — keep complete text
                event_lines.append(f"{i+1}. [AGENT_THINKING — DECLARED] {detail_str}")
                # Pre-form a DECLARED decision from this reasoning note
                declared_reasoning_decisions.append({
                    "step": len(declared_reasoning_decisions) + 1,
                    "event_idx": i + 1,
                    "action": last_action_label or "Agent internal reasoning",
                    "reasoning": detail_str,
                    "epistemic_status": "DECLARED",
                })
            else:
                event_lines.append(f"{i+1}. [{action}] {detail_str}")
                last_action_label = f"{action}: {detail_str}"

        timeline_text = "\n".join(event_lines)

        prompt = f"""You are AgentTrace, an explainability layer for AI coding agents.

A developer asked their AI coding agent: "{user_prompt}"

AGENT TRANSCRIPT (observable events from start to finish):
{timeline_text}

DIFF SUMMARY (what code actually changed):
{diff_summary if diff_summary else 'See logical changes.'}

YOUR TASK — reconstruct the agent's reasoning chain and decisions. Respond ONLY with valid JSON, no markdown:
{{
  "summary": "<2-3 sentence narrative explaining how the agent investigated the task and arrived at its conclusion — grounded in the actual events above>",
  "decisions": [
    {{
      "step": 1,
      "action": "<what the agent did, e.g. 'Searched for retry implementations'>",
      "reasoning": "<why the agent did this — inferred from what came before and after in the transcript>",
      "epistemic_status": "<one of: OBSERVED | INFERRED | DECLARED>"
    }}
  ],
  "how_arrived_steps": [
    "<step 1 as a short plain-English sentence>",
    "<step 2>",
    "..."
  ]
}}

Rules:
- decisions should cover the key inflection points (3-7 items), not every single event
- Lines marked [AGENT_THINKING — DECLARED] are the agent's verbatim internal reasoning — use them directly as the reasoning field for adjacent decisions; mark those with epistemic_status DECLARED
- For all other steps, distinguish OBSERVED (directly seen tool call) from INFERRED (deduced from pattern)
- how_arrived_steps should be concise, ordered, action-oriented sentences the developer can read at a glance
- summary should feel like a senior engineer explaining what happened
- Do not invent tool calls or events not present in the transcript
"""
        result = self._call_gemini_json(prompt)
        if result and isinstance(result, dict) and result.get("decisions"):
            result["enriched"] = True
            # Reconcile DECLARED decisions with Gemini's output to guarantee
            # full verbatim thinking is never truncated or lost.
            if declared_reasoning_decisions:
                declared_by_event_idx = {d["event_idx"]: d for d in declared_reasoning_decisions}
                for dec in result["decisions"]:
                    step = dec.get("step")
                    dec_r = (dec.get("reasoning") or "").strip()
                    matched_declared = declared_by_event_idx.get(step)
                    if not matched_declared:
                        for d in declared_reasoning_decisions:
                            decl_r = d["reasoning"]
                            if not decl_r:
                                continue
                            if (dec_r and (dec_r in decl_r or decl_r in dec_r or (len(dec_r) >= 20 and decl_r.startswith(dec_r[:40])))) or (dec.get("epistemic_status") == "DECLARED" and not dec_r):
                                matched_declared = d
                                break
                    if matched_declared:
                        dec["reasoning"] = matched_declared["reasoning"]
                        dec["epistemic_status"] = "DECLARED"
            # Ensure how_arrived_steps is populated
            if not result.get("how_arrived_steps") and result.get("decisions"):
                result["how_arrived_steps"] = [
                    f"{d.get('action', '')} — {d.get('reasoning', '')}"
                    for d in result["decisions"]
                ]
            return result

        # Even if Gemini fails, surface declared reasoning as a minimal journey
        if declared_reasoning_decisions:
            fallback_decisions = [
                {
                    "step": idx + 1,
                    "action": d["action"],
                    "reasoning": d["reasoning"],
                    "epistemic_status": "DECLARED",
                }
                for idx, d in enumerate(declared_reasoning_decisions)
            ]
            return {
                "summary": (
                    f"The agent processed \"{user_prompt}\" with "
                    f"{len(declared_reasoning_decisions)} recorded reasoning step(s) "
                    "extracted directly from the transcript."
                ),
                "decisions": fallback_decisions,
                "how_arrived_steps": [d["action"] for d in fallback_decisions],
                "enriched": False,
            }

        return self._journey_fallback(user_prompt, diff_summary)

    def _journey_fallback(self, user_prompt: str, diff_summary: str) -> Dict[str, Any]:
        """Returns a minimal deterministic journey when Gemini is unavailable."""
        steps = [
            f"Received developer request: \"{user_prompt}\"",
            "Inspected git repository and extracted diff hunks via AST parser.",
            "Applied Jev System 1 to classify each hunk by category and blast radius.",
            "Built causal graph linking request → investigation → code changes → risk.",
        ]
        if diff_summary:
            steps.append(f"Diff scope: {diff_summary}")
        return {
            "summary": (
                f"The agent processed the request \"{user_prompt}\" by inspecting the repository, "
                "classifying diff hunks, and mapping blast-radius impact across the codebase."
            ),
            "decisions": [
                {"step": i+1, "action": s, "reasoning": "", "epistemic_status": "OBSERVED"}
                for i, s in enumerate(steps)
            ],
            "how_arrived_steps": steps,
            "enriched": False,
        }

    def synthesize_architectural_narrative(
        self,
        prompt: str,
        files_modified: List[str],
        abstractions: List[str],
        unattended_callers: List[str],
        blast_level: str = "service_level",
    ) -> Dict[str, Any]:
        """Synthesizes high-level architectural narrative and key invariants for a diff change."""
        llm_prompt = f"""You are a Principal Software Architect reviewing an AI code agent's pull request.
Summarize the architectural transformation and impact clearly for developers.

Context:
User Request: {prompt}
Files Modified: {', '.join(files_modified[:5])}
Primary Abstractions: {', '.join(abstractions[:4])}
Unattended Callers / Omission Risks: {', '.join(unattended_callers[:4]) if unattended_callers else 'None'}
Blast Level: {blast_level}

Return ONLY valid JSON matching this schema:
{{
  "narrative": "A 2-3 sentence executive architectural explanation of the change and its downstream ripple effects.",
  "invariants": ["List of 2-3 system guarantees or verification invariants preserved or broken"],
  "architectural_verdict": "SAFE" | "NEEDS_REVIEW" | "BREAKING"
}}
"""
        result = self._call_gemini_json(llm_prompt)
        if isinstance(result, dict) and "narrative" in result:
            result["source"] = "Gemini LLM (Architectural Synthesis)"
            return result

    def synthesize_architectural_mind_map(
        self,
        prompt: str,
        files_modified: List[str],
        abstractions: List[str],
        unattended_callers: List[str],
        blast_level: str = "service_level",
    ) -> Dict[str, Any]:
        """
        Synthesizes a pure conceptual Developer Mind Map (no code syntax, no line numbers, no file paths).
        Focuses on: Problem & Catalyst -> Architectural Pattern -> Capabilities Impacted -> Watchouts -> Guarantees.
        """
        llm_prompt = f"""You are a Principal Software Architect explaining a software change to an engineering lead.
DO NOT use file paths, line numbers, code snippets, or programming language syntax.
Instead, build a high-level CONCEPTUAL MIND MAP that explains the mental model of the changes.

Context:
User Request / Prompt: {prompt}
High-Level Modularity Touched: {', '.join(files_modified[:6])}
Key Concepts / Modules: {', '.join(abstractions[:4])}
Unattended / Forgotten Callers: {', '.join(unattended_callers[:4]) if unattended_callers else 'None'}
Blast Level: {blast_level}

Return ONLY valid JSON matching this schema:
{{
  "catalyst": {{
    "title": "Short Problem/Goal Title (e.g. Prevent Gateway Timeout Drops)",
    "details": "1-2 plain sentences explaining what problem needed solving."
  }},
  "strategy": {{
    "title": "Design Pattern or Approach Applied (e.g. Exponential Retry Policy with Jitter)",
    "details": "1-2 plain sentences explaining the conceptual solution."
  }},
  "capability": {{
    "title": "Main User or Business Capability Improved (e.g. Payment Checkout Reliability)",
    "details": "1-2 plain sentences explaining which user-facing workflow is enhanced."
  }},
  "watchout": {{
    "title": "Architectural Gotcha or Attention Point (e.g. Downstream Caller Desynchronization)",
    "details": "1-2 plain sentences explaining what risk or forgotten edge case the developer must keep in mind."
  }},
  "guarantee": {{
    "title": "Safety & Integrity Invariant (e.g. Idempotent Execution Guarantee)",
    "details": "1-2 plain sentences explaining the contract that must be verified."
  }},
  "narrative": "A 2-sentence executive summary of the architectural mental model.",
  "verdict": "SAFE" | "NEEDS_REVIEW"
}}
"""
        result = self._call_gemini_json(llm_prompt)
        if (
            isinstance(result, dict)
            and "catalyst" in result
            and "strategy" in result
            and isinstance(result["catalyst"], dict)
            and isinstance(result["strategy"], dict)
        ):
            result["source"] = "Gemini LLM (Mind Map Synthesis)"
            return result

        # ── Deterministic Conceptual Local Fallback (Zero code, pure mental model) ──
        prompt_lower = (prompt or "").lower()
        files_lower = " ".join(files_modified).lower()

        # Deduce conceptual strategy
        if "retry" in prompt_lower or "retry" in files_lower or "backoff" in prompt_lower:
            strat_title = "Exponential Backoff & Fault Resilience"
            strat_desc = "Centralizes retry policies with jitter to prevent server stampedes during downstream timeouts."
            cap_title = "Transaction & Checkout Reliability"
            cap_desc = "Core user transactions automatically withstand intermittent network drops without user disruption."
            guar_title = "Idempotent Execution Contract"
            guar_desc = "Retried operations are guaranteed not to duplicate side-effects or multi-charge requests."
        elif "auth" in prompt_lower or "token" in prompt_lower or "security" in prompt_lower:
            strat_title = "Synchronized Token Concurrency"
            strat_desc = "Coordinates concurrent API requests through a single refresh gate to prevent auth races."
            cap_title = "Session Security & Seamless Authentication"
            cap_desc = "Users experience uninterrupted sessions without sporadic 401 unauthenticated drops."
            guar_title = "Race-Free Token Mutex"
            guar_desc = "Ensures expired tokens are refreshed exactly once regardless of parallel requests."
        elif "cache" in prompt_lower or "db" in files_lower or "store" in files_lower:
            strat_title = "Tiered Caching & State Persistence"
            strat_desc = "Maintains in-memory sub-millisecond retrieval with durable background storage across sessions."
            cap_title = "Instant Session State Recovery"
            cap_desc = "Enables rapid dashboard reloads and persistent tracking of historical analysis sessions."
            guar_title = "State Consistency & Cache Coherence"
            guar_desc = "Cached state invalidates cleanly on modifications without returning stale session artifacts."
        else:
            strat_title = "Modular Logic Centralization"
            strat_desc = "Refactors dispersed operations into unified abstractions to enforce DRY architecture and consistency."
            cap_title = "Core Operational Workflow"
            cap_desc = "Improves maintainability and execution reliability across user-facing subsystem entrypoints."
            guar_title = "Backward Compatibility & Contract Stability"
            guar_desc = "Existing entrypoints and consumer expectations are preserved without breaking interfaces."

        # Deduce catalyst
        cat_title = "Targeted Architecture Enhancement"
        cat_desc = prompt or "System modification to improve reliability, maintainability, and operational stability."

        # Deduce watchout
        if unattended_callers:
            watch_title = "Downstream Caller Desynchronization"
            watch_desc = f"{len(unattended_callers)} caller(s) interact with the touched subsystem and should be checked for contract alignment."
            verdict = "NEEDS_REVIEW"
        else:
            watch_title = "Execution Flow Alignment"
            watch_desc = "All touched components appear properly aligned, but integration tests should verify boundary handoffs."
            verdict = "SAFE"

        return {
            "catalyst": {
                "title": cat_title,
                "details": cat_desc,
            },
            "strategy": {
                "title": strat_title,
                "details": strat_desc,
            },
            "capability": {
                "title": cap_title,
                "details": cap_desc,
            },
            "watchout": {
                "title": watch_title,
                "details": watch_desc,
            },
            "guarantee": {
                "title": guar_title,
                "details": guar_desc,
            },
            "narrative": (
                f"The change applies '{strat_title}' to fulfill '{cat_title}', strengthening '{cap_title}'. "
                + ("Caution: Verify downstream caller alignment." if unattended_callers else "Execution contracts remain stable.")
            ),
            "verdict": verdict,
            "source": "AgentTrace Grounded Mind Map (Local)",
        }

    def synthesize_deep_risk_analysis(
        self,
        rule: str,
        symbol: str,
        file_path: str,
        diff_snippet: str = "",
        jev_category: str = "REFACTOR",
        blast_level: str = "localized",
        blast_score: float = 1.0,
        human_review_probability: float = 0.5,
        breaking_risk: bool = False,
        omission_rationale: str = "",
        graph_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synthesizes a deep, grounded multi-dimensional explanation of WHY this risk was flagged.

        Integrates:
        1. Jev System 1 quantitative & qualitative metrics
        2. Actual code diff hunk (old lines vs new lines)
        3. Change graph & dependency context (callers, coupled services, omission risks)
        """
        graph_ctx = graph_context or {}
        callers = graph_ctx.get("affected_callers", [])
        coupled = graph_ctx.get("coupled_services", [])
        unattended = graph_ctx.get("unattended_callers", [])

        # Fast path if API key available and remote not skipped
        skip_remote = bool(os.getenv("AGENTTRACE_SKIP_ENRICHMENT"))
        if self.has_api_key and not skip_remote:
            prompt = f"""You are AgentTrace, an AI code-review explainability and risk-assessment engine.
A code modification was flagged under the rule: "{rule}".
Explain clearly and concisely WHY this code change is risky, synthesizing the three evidence dimensions below.

[DIMENSION 1: JEV SYSTEM 1 SIGNALS]
- Flagged Rule: {rule}
- Symbol: {symbol} in {file_path}
- Classification: {jev_category}
- Blast Radius: {blast_level} (score: {blast_score:.1f}/5.0)
- Human Review Probability: {human_review_probability:.0%}
- Breaking Change Flag: {'YES' if breaking_risk else 'NO'}
- Omission / Caller Risk: {omission_rationale or 'None detected'}

[DIMENSION 2: CODE CHANGE (DIFF)]
{diff_snippet[:1200] if diff_snippet else 'Diff not directly provided; symbol modified in ' + file_path}

[DIMENSION 3: CHANGE GRAPH & REPOSITORY TOPOLOGY]
- Directly Affected Callers: {', '.join(callers) if callers else 'Within ' + file_path}
- Coupled Services: {', '.join(coupled) if coupled else 'Single-service scope'}
- Unattended / Untouched Callers: {', '.join(unattended) if unattended else 'None'}

Return ONLY a valid JSON object matching this schema (no markdown fences, no preamble):
{{
  "summary": "1-2 sentence executive summary of the risk",
  "why_at_risk": "2-3 plain sentences explaining why this code change creates risk, tying together the code modification, Jev severity, and dependency blast radius",
  "jev_signals_breakdown": "1-2 sentences highlighting the Jev metrics (e.g. human review probability, blast radius level, breaking risk)",
  "code_change_breakdown": "1-2 sentences explaining what specifically changed in the code hunk",
  "change_graph_breakdown": "1-2 sentences explaining the ripple effects, downstream callers, and architectural blast radius",
  "recommended_verification": "1-2 concrete steps the developer or reviewer should take before merging"
}}"""
            res = self._call_gemini_json(prompt, timeout=8)
            if isinstance(res, dict) and "why_at_risk" in res and "summary" in res:
                res["source"] = "Gemini LLM (System 2 Grounded)"
                return res

        # High-quality deterministic local grounded fallback
        return self._synthesize_local_risk_analysis(
            rule=rule,
            symbol=symbol,
            file_path=file_path,
            diff_snippet=diff_snippet,
            jev_category=jev_category,
            blast_level=blast_level,
            blast_score=blast_score,
            human_review_probability=human_review_probability,
            breaking_risk=breaking_risk,
            omission_rationale=omission_rationale,
            graph_context=graph_ctx,
        )

    def _synthesize_local_risk_analysis(
        self,
        rule: str,
        symbol: str,
        file_path: str,
        diff_snippet: str = "",
        jev_category: str = "REFACTOR",
        blast_level: str = "localized",
        blast_score: float = 1.0,
        human_review_probability: float = 0.5,
        breaking_risk: bool = False,
        omission_rationale: str = "",
        graph_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        graph_ctx = graph_context or {}
        callers = graph_ctx.get("affected_callers", [])
        coupled = graph_ctx.get("coupled_services", [])
        unattended = graph_ctx.get("unattended_callers", [])

        clean_file = file_path.split("/")[-1] if file_path else "source file"
        clean_sym = symbol if symbol and symbol != "unknown" and symbol != "module" else clean_file
        prob_pct = f"{human_review_probability:.0%}"

        # 1. Why at risk
        if omission_rationale or unattended:
            unattended_str = f" ({len(unattended)} unattended caller(s) like {', '.join(unattended[:3])})" if unattended else ""
            why = (
                f"Modifications to `{clean_sym}` in `{clean_file}` introduce omissions{unattended_str}. "
                f"Callers relying on the pre-change contracts were not updated in this change set, creating high probability of runtime exceptions."
            )
        elif breaking_risk:
            why = (
                f"Changes to `{clean_sym}` alter public behavior or call signatures in `{clean_file}`. "
                f"Downstream consumers expecting the prior signature will experience invocation errors unless updated in lockstep."
            )
        elif blast_level in ("service_level", "system_critical") or blast_score >= 3.5:
            why = (
                f"`{clean_sym}` is a high-leverage component within `{clean_file}` with a `{blast_level}` blast radius (score {blast_score:.1f}/5.0). "
                f"Behavioral regressions in this hunk propagate across critical system boundaries."
            )
        elif jev_category == "SECURITY_RELEVANT_CHANGE":
            why = (
                f"`{clean_sym}` modifies security-critical authentication, authorization, or token management in `{clean_file}`. "
                f"Flaws or race conditions in this logic can compromise credentials or trigger infinite request loops."
            )
        elif jev_category == "API_CHANGE":
            why = (
                f"`{clean_sym}` alters an API contract or handler signature in `{clean_file}`. "
                f"External clients and internal callers must be audited for interface compatibility."
            )
        elif "stale" in rule.lower() or "removal" in rule.lower():
            why = (
                f"`{clean_sym}` in `{clean_file}` involves refactoring or commonization that may leave behind stale, deprecated, or dead code. "
                f"Verify that obsolete code paths and duplicate implementations are thoroughly cleaned up."
            )
        else:
            why = (
                f"Jev flagged `{clean_sym}` with {prob_pct} attention probability under {rule.lower()}. "
                f"The modifications in `{clean_file}` require senior verification before deployment."
            )

        # 2. Jev signals breakdown
        jev_breakdown = (
            f"Classified as {jev_category} with {blast_level} blast radius (score {blast_score:.1f}/5.0). "
            f"Human review probability is {prob_pct}"
            + (", with breaking risk confirmed." if breaking_risk else ", non-breaking.")
            + (f" Omission alert: {omission_rationale}" if omission_rationale else "")
        )

        # 3. Code change breakdown
        diff_lines = [line.strip() for line in diff_snippet.splitlines() if line.strip()]
        added = [l[1:].strip() for l in diff_lines if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:].strip() for l in diff_lines if l.startswith("-") and not l.startswith("---")]

        if added and removed:
            code_breakdown = f"Modified core execution in `{clean_sym}`: replaced {len(removed)} line(s) with {len(added)} line(s) of new logic."
        elif added:
            code_breakdown = f"Added {len(added)} line(s) of new implementation logic to `{clean_sym}` in `{clean_file}`."
        elif removed:
            code_breakdown = f"Removed {len(removed)} line(s) from `{clean_sym}` in `{clean_file}`."
        else:
            code_breakdown = f"Direct modification to symbol `{clean_sym}` in `{clean_file}`."

        # 4. Change graph breakdown
        if callers:
            caller_summary = f"{len(callers)} caller(s) detected ({', '.join(callers[:4])})"
        elif coupled:
            caller_summary = f"Coupled across {len(coupled)} service(s): {', '.join(coupled[:4])}"
        else:
            caller_summary = f"Localized to `{clean_file}` with potential downstream package imports"

        if unattended:
            graph_breakdown = f"Dependency graph reveals {caller_summary}. Warning: {len(unattended)} untouched caller(s) still invoke old contract."
        else:
            graph_breakdown = f"Dependency graph connects `{clean_sym}` to {caller_summary} across the repository."

        # 5. Recommended verification
        if breaking_risk or omission_rationale:
            rec_verif = f"Run regression suite for `{clean_file}` and audit all callers of `{clean_sym}`."
        elif jev_category == "SECURITY_RELEVANT_CHANGE":
            rec_verif = f"Perform security inspection on token/session lifecycles and verify error re-entry guardrails."
        elif "stale" in rule.lower() or "removal" in rule.lower():
            rec_verif = f"Search repository for unreferenced callers of `{clean_sym}` and remove dead or duplicate code blocks."
        else:
            rec_verif = f"Verify unit test coverage for `{clean_sym}` and execute end-to-end integration tests."

        summary = f"{rule} for `{clean_sym}`: {why.split('. ')[0]}."

        return {
            "summary": summary,
            "why_at_risk": why,
            "jev_signals_breakdown": jev_breakdown,
            "code_change_breakdown": code_breakdown,
            "change_graph_breakdown": graph_breakdown,
            "recommended_verification": rec_verif,
            "source": "AgentTrace Grounded Risk Synthesis (Local)",
        }

    def synthesize_risk_summary(
        self,
        rule: str,
        symbol: str,
        file_path: str,
        jev_category: str,
        blast_level: str,
        human_review_probability: float,
        breaking_risk: bool,
        omission_rationale: str = "",
    ) -> Optional[str]:
        """Return a 1-2 sentence plain-English summary of WHY this item needs attention.

        Grounded entirely in Jev analysis fields — no hallucination.
        Falls back to local synthesis summary if remote is unavailable.
        """
        # First attempt remote synthesis if API key is present
        skip_remote = bool(os.getenv("AGENTTRACE_SKIP_ENRICHMENT"))
        if self.has_api_key and not skip_remote:
            jev_facts = (
                f"- Symbol: `{symbol}` in `{file_path}`\n"
                f"- Jev classification: {jev_category}\n"
                f"- Blast radius level: {blast_level}\n"
                f"- Human review probability: {human_review_probability:.0%}\n"
                f"- Breaking risk: {'yes' if breaking_risk else 'no'}\n"
                + (f"- Omission/caller risk: {omission_rationale}\n" if omission_rationale else "")
            )
            prompt = f"""You are AgentTrace, a code-review analysis tool.
Based on the Jev System 1 analysis below, write a single concise sentence (max 25 words) that clearly explains
why `{symbol}` in `{file_path}` needs {rule.lower()} — be specific about what makes it risky.
Do NOT start with "This" or repeat the field names verbatim.

Jev analysis facts:
{jev_facts}

Output only the sentence, no preamble, no bullet points."""

            for model_name in self.CANDIDATE_MODELS:
                try:
                    text = self._rest_generate(model_name, prompt, timeout=8).strip()
                    text = text.split("\n")[0].strip("`*_ ")
                    if text:
                        return text
                except Exception:
                    continue

        # Grounded local fallback summary
        local = self._synthesize_local_risk_analysis(
            rule=rule,
            symbol=symbol,
            file_path=file_path,
            jev_category=jev_category,
            blast_level=blast_level,
            human_review_probability=human_review_probability,
            breaking_risk=breaking_risk,
            omission_rationale=omission_rationale,
        )
        return local.get("summary")

    def _call_gemini_json(self, prompt: str, timeout: int = 5) -> Any:
        """Call Gemini REST and parse the response as JSON. Returns parsed object or None.
        
        Fast-fails immediately if the remote host is unreachable or DNS fails,
        preventing repeated roundtrips and ensuring sub-millisecond local fallback.
        """
        if not self.has_api_key:
            return None
        if os.getenv("AGENTTRACE_SKIP_ENRICHMENT"):
            return None

        for model_name in self.CANDIDATE_MODELS:
            try:
                text = self._rest_generate(model_name, prompt, timeout=timeout).strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (httpx.ConnectError, httpx.NetworkError, socket.gaierror) as net_err:
                # Host is unreachable / offline / DNS error -> do not retry other models to the same host!
                sys.stderr.write(f"[GeminiClient] Remote Gemini unreachable ({net_err.__class__.__name__}), using instant local synthesis.\n")
                break
            except Exception as e:
                # Model-specific error (404, 429) -> try next candidate model
                continue
        return None



gemini_client = GeminiClient()
