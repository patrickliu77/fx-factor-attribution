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
import re
import random
import time
from datetime import datetime, timezone
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
    """Safe diagnostic fields only. Provider text and chained exceptions stay out."""

    def __init__(self, category, *, stage='request', http_status=None, retryable=False):
        self.diagnostic = {'category': category, 'stage': stage, 'retryable': retryable}
        if http_status is not None:
            self.diagnostic['http_status'] = http_status
        super().__init__(category)


def failure_details(exc):
    return dict(exc.diagnostic) if isinstance(exc, GenerationError) else {
        'category': 'unexpected_error', 'stage': 'generation', 'retryable': False}


def http_failure(code, body):
    """Read error details in memory; persist classification, never their contents."""
    try:
        error = json.loads(body).get('error', {})
        message = str(error.get('message', '')).lower()
        details = error.get('details', [])
        quota_ids = ' '.join(str(v.get('quotaId', '')) for d in details for v in d.get('violations', [])).lower()
        zero = bool(re.search(r'limit:\s*0\b', message))
        daily = 'perday' in quota_ids or 'per day' in message
        per_minute = 'perminute' in quota_ids
    except (ValueError, TypeError, AttributeError):
        zero = daily = per_minute = False
    category = {400: 'invalid_request', 401: 'authentication_failed', 403: 'permission_denied',
                404: 'model_unavailable', 408: 'request_timeout'}.get(code, 'http_error')
    retryable = code in {408, 500, 502, 503, 504}
    if code in {500, 502, 503, 504}:
        category = 'service_unavailable'
    if code == 429:
        category = 'quota_exhausted' if zero or daily else 'rate_limited' if per_minute else 'quota_or_rate_limit'
        retryable = per_minute and not (zero or daily)
    result = GenerationError(category, http_status=code, retryable=retryable)
    # Respect a provider's stated retry delay. Long waits are deferred to a
    # future run rather than consuming the current briefing's time budget.
    try:
        waits = [float(d['retryDelay'][:-1]) for d in details
                 if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?s', str(d.get('retryDelay', '')))]
        if waits:
            result.diagnostic['retry_after_seconds'] = min(max(waits), 86400)
    except (UnboundLocalError, TypeError, ValueError, AttributeError):
        pass
    return result


class GeminiClient:
    """Implements compose's LLMClient protocol."""

    def __init__(self, model: str = LLM_MODEL, api_key: str | None = None,
                 timeout: int = TIMEOUT_S, max_requests: int = 6, max_attempts: int = 2,
                 sleeper=time.sleep):
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise GenerationError('missing_api_key', stage='configuration')
        if not 1 <= max_requests <= 6 or not 1 <= max_attempts <= 2:
            raise ValueError('invalid_request_budget')
        self._key = key
        self.model = model
        self.timeout = timeout
        self.calls: list[dict] = []
        self.attempts: list[dict] = []
        self.max_requests, self.max_attempts = max_requests, max_attempts
        self.sleeper = sleeper

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
            "request_attempts": len(self.attempts),
            "attempts": self.attempts,
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
        url = f"{API_BASE}/{self.model}:generateContent"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key}, method="POST")

        body = None
        for attempt in range(self.max_attempts):
            if len(self.attempts) >= self.max_requests:
                raise GenerationError('request_budget_exhausted', stage='budget')
            record = {'started_at': datetime.now(timezone.utc).isoformat(), 'state': 'started',
                      'request_number': len(self.attempts)+1}
            self.attempts.append(record)
            started = time.monotonic()
            error = None
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    record['http_status'] = response.status
                    body = json.loads(response.read())
                if not isinstance(body, dict):
                    raise ValueError('invalid_response_shape')
                record['state'] = 'response_received'
            except urllib.error.HTTPError as exc:
                try:
                    body_error = exc.read()
                except Exception:
                    body_error = b'{}'
                error = http_failure(exc.code, body_error)
                retry_after = (exc.headers or {}).get('Retry-After', '')
                if re.fullmatch(r'[0-9]{1,5}', str(retry_after)):
                    error.diagnostic['retry_after_seconds'] = min(int(retry_after), 86400)
            except (TimeoutError, urllib.error.URLError):
                # Delivery may have occurred. Do not resend an ambiguous request.
                error = GenerationError('transport_failure', stage='transport')
            except Exception:
                error = GenerationError('invalid_response', stage='response')
            finally:
                record['elapsed_seconds'] = round(time.monotonic()-started, 3)
                record['finished_at'] = datetime.now(timezone.utc).isoformat()
            if error is None:
                break
            record.update(state='failed', **error.diagnostic)
            if not error.diagnostic['retryable'] or attempt+1 >= self.max_attempts or len(self.attempts) >= self.max_requests:
                raise error from None
            delay = max(2 ** attempt, error.diagnostic.get('retry_after_seconds', 0))
            if delay > 10:
                record['retry_deferred'] = True
                raise error from None
            delay += random.uniform(0, .25)
            record['retry_delay_seconds'] = delay
            self.sleeper(delay)

        usage = body.get('usageMetadata') or {}
        self.calls.append({k: usage[k] for k in ('promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount')
                           if isinstance(usage, dict) and type(usage.get(k)) is int and usage[k] >= 0})

        candidates = body.get("candidates") or []
        if not candidates:
            self.attempts[-1].update(state='failed', category='no_candidate', stage='output')
            raise GenerationError('no_candidate', stage='output')
        if not isinstance(candidates, list) or not isinstance(candidates[0], dict):
            self.attempts[-1].update(state='failed', category='invalid_response', stage='output')
            raise GenerationError('invalid_response', stage='output')
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason not in (None, "STOP"):
            # Safety blocks, length overruns and the like surface here; they must not
            # be parsed on as if they were normal output
            category = 'output_limit' if reason == 'MAX_TOKENS' else 'output_blocked'
            self.attempts[-1].update(state='failed', category=category, stage='output')
            raise GenerationError(category, stage='output')

        try:
            text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []))
        except (TypeError, AttributeError):
            self.attempts[-1].update(state='failed', category='invalid_response', stage='output')
            raise GenerationError('invalid_response', stage='output') from None
        if not text.strip():
            self.attempts[-1].update(state='failed', category='empty_output', stage='output')
            raise GenerationError('empty_output', stage='output')
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            self.attempts[-1].update(state='failed', category='invalid_json', stage='output')
            raise GenerationError('invalid_json', stage='output') from None
        self.attempts[-1]['state'] = 'completed'
        return parsed
