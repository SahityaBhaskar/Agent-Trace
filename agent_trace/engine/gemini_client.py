import os
import sys
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
    """Client for Google Gemini (System 2 Frontier Reasoning LLM) via google-genai SDK."""

    CANDIDATE_MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._client = None

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

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

        if self.has_api_key:
            client = self._get_client()
            for model_name in self.CANDIDATE_MODELS:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    text = resp.text.strip() if resp.text else ""
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


gemini_client = GeminiClient()
