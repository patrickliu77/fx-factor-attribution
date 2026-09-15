"""Explicit GitHub OIDC -> Azure Storage token exchange, with an in-memory cache.

No account setup, CLI login, token files, environment fallback or HTTP retries.
Entra validates the assertion and the configured federation trust. This client
does not decode provider access tokens or treat decoded JWT claims as authority.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit, urlencode

from .journal import unique_object
from .state import StateError

AUDIENCE = "api://AzureADTokenExchange"
SCOPE = "https://storage.azure.com/.default"
MARGIN = 120
MAX_RESPONSE = 65536
GUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
BEARER = r"[A-Za-z0-9._~+/-]{20,16384}={0,2}"


def oidc_url(value):
    try:
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        # Public GitHub-hosted runners only. Do not forward the job credential
        # to arbitrary endpoints supplied through workflow configuration.
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.actions\.githubusercontent\.com", parsed.netloc)
                or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*_apis/oidc/token", parsed.path)
                or parsed.query != "api-version=2.0" or "#" in value):
            raise ValueError()
        return value + "&" + urlencode({"audience": AUDIENCE})
    except (TypeError, ValueError):
        raise StateError("invalid_oidc_endpoint") from None


def request_token():
    return os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")


class GitHubStorageToken:
    def __init__(self, tenant_id, client_id, request_url, *,
                 request_token_provider=request_token, transport=None, clock=time.monotonic):
        if (not all(isinstance(v, str) and re.fullmatch(GUID, v) for v in (tenant_id, client_id))
                or not callable(request_token_provider) or not callable(clock)):
            raise StateError("invalid_federation_settings")
        self._oidc_url = oidc_url(request_url)
        self._exchange_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        self._client_id = client_id
        self._request_token, self._transport, self._clock = request_token_provider, transport, clock
        self._cached, self._started, self._expires = None, 0, 0
        self._lock = threading.Lock()

    def _now(self):
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise StateError("invalid_identity_clock")
        return value

    def _json(self, method, url, *, headers=None, data=None):
        response = None
        try:
            if self._transport is None:
                import requests
                self._transport = requests.Session()
                self._transport.trust_env = False
            response = self._transport.request(method, url,
                headers={"Accept": "application/json", "Accept-Encoding": "identity",
                         "Cache-Control": "no-store", **(headers or {})},
                data=data, timeout=(5, 30), stream=True, allow_redirects=False)
            if (response.status_code != 200 or response.headers.get("Content-Encoding")
                    or response.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"):
                raise ValueError()
            chunks, size = [], 0
            for chunk in response.iter_content(8192):
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ValueError()
                chunks.append(chunk)
            value = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
            if not isinstance(value, dict) or "error" in value:
                raise ValueError()
            return value
        except Exception:
            raise StateError("identity_request_failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def __call__(self):
        with self._lock:
            try:
                started = self._now()
                if self._cached and self._started <= started < self._expires - MARGIN:
                    return self._cached
                # No stale-token fallback if a refresh fails.
                self._cached = None
                credential = self._request_token()
                if not isinstance(credential, str) or not re.fullmatch(BEARER, credential):
                    raise StateError("oidc_job_token_required")
                assertion = self._json("GET", self._oidc_url,
                    headers={"Authorization": "Bearer " + credential}).get("value")
                if (not isinstance(assertion, str) or not 20 <= len(assertion) <= 16384
                        or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", assertion)):
                    raise StateError("invalid_oidc_assertion")
                response = self._json("POST", self._exchange_url, data={
                    "client_id": self._client_id, "scope": SCOPE, "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": assertion})
                token, lifetime = response.get("access_token"), response.get("expires_in")
                if (response.get("token_type", "").lower() != "bearer"
                        or not isinstance(token, str) or not re.fullmatch(BEARER, token)
                        or type(lifetime) is not int or not MARGIN < lifetime <= 86400):
                    raise StateError("invalid_storage_token")
                finished = self._now()
                if not started <= finished < started + lifetime - MARGIN:
                    raise StateError("storage_token_too_old")
                self._cached, self._started, self._expires = token, started, started + lifetime
                return token
            except StateError:
                self._cached = None
                raise
            except Exception:
                self._cached = None
                raise StateError("identity_unavailable") from None
