"""Gemini client (SPEC_phase3 §3-4, revised 2026-09-01).

Generation only. **Retrieval is decoupled from generation** and goes through Google
News RSS, not through here -- because this key's search grounding quota is 0 (plain
generation works, grounding returns 429 continuously: a quota, not rate limiting).
See SPEC §3.1.

No SDK, plain REST: one endpoint and one request shape, which removes a whole layer
of version-compatibility surface. The key is read from the environment only, and is
never written to disk or into an artifact.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from .compose import LLM_MODEL, to_gemini_schema

log = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
API_KEY_ENV = "GEMINI_API_KEY"
TIMEOUT_S = 180

# Models measured to work on the free tier. The Pro series has limit: 0; do not try
# to move up to it.
FREE_TIER_MODELS = (
    "models/gemini-3.5-flash",
    "models/gemini-3-flash-preview",
    "models/gemini-flash-latest",
)


class GenerationError(RuntimeError):
    """Generation failed. The caller records it and moves on; no retry."""


class GeminiClient:
    """Implements compose's LLMClient protocol."""

    def __init__(self, model: str = LLM_MODEL, api_key: str | None = None,
                 timeout: int = TIMEOUT_S):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise GenerationError(
                f"{API_KEY_ENV} is not set. The key is read from the environment "
                f"only and is never written to any file.")
        self._key = key
        self.model = model
        self.timeout = timeout
        self.calls: list[dict] = []

    # --------------------------------------------------------------- accounting
    @property
    def totals(self) -> dict:
        return {
            "provider": "gemini",
            "model": self.model,
            "calls": len(self.calls),
            "prompt_tokens": sum(c.get("promptTokenCount") or 0 for c in self.calls),
            "output_tokens": sum(c.get("candidatesTokenCount") or 0 for c in self.calls),
            "thought_tokens": sum(c.get("thoughtsTokenCount") or 0 for c in self.calls),
            "total_tokens": sum(c.get("totalTokenCount") or 0 for c in self.calls),
            # Free tier, no metered billing. The field is kept to match the artifact
            # schema, not as a token placeholder
            "token_cost_usd": 0.0,
            "searches": 0,
            "detail": self.calls,
        }

    # --------------------------------------------------------------- generation
    def complete(self, system: str, user: str, schema: dict) -> dict:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema),
            },
        }
        url = f"{API_BASE}/{self.model}:generateContent?key={self._key}"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise GenerationError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GenerationError(f"{type(exc).__name__}: {exc}") from exc

        self.calls.append(body.get("usageMetadata") or {})

        candidates = body.get("candidates") or []
        if not candidates:
            raise GenerationError(f"no candidate: {json.dumps(body)[:300]}")
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason not in (None, "STOP"):
            # Safety blocks, length overruns and the like surface here; they must not
            # be parsed on as if they were normal output
            raise GenerationError(f"finishReason={reason}")

        text = "".join(
            part.get("text", "")
            for part in (candidate.get("content") or {}).get("parts", [])
        )
        if not text.strip():
            raise GenerationError("structured output is empty")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenerationError(f"output is not valid JSON: {exc}; first 200 chars: {text[:200]}")
