from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from fxdash.cloud import identity as I, azure_blob as A
from fxdash.cloud.state import StateError
from test_cloud_azure import Response, Backend, TOKEN

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
URL = "https://vstoken.actions.githubusercontent.com/tenant/job/_apis/oidc/token?api-version=2.0"
JOB_TOKEN = "offline-job-request-token"
ASSERTION = "offlineheader.offlinepayload.offlinesignature"


class Transport:
    def __init__(self):
        self.calls, self.responses = [], []
        self.extra, self.lifetime = {}, 3600
        self.response = None

    def request(self, method, url, **kwargs):
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["timeout"] == (5, 30)
        self.calls.append((method, url, kwargs))
        if self.response:
            response, self.response = self.response, None
        else:
            value = ({"value": ASSERTION} if method == "GET" else
                     {"token_type": "Bearer", "access_token": TOKEN, "expires_in": self.lifetime, **self.extra})
            response = Response(body=json.dumps(value).encode(), headers={"Content-Type": "application/json"})
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("No real identity request"))


def provider(transport=None, **kw):
    return I.GitHubStorageToken(TENANT, CLIENT, URL, transport=transport or Transport(),
                               request_token_provider=lambda: JOB_TOKEN, **kw)


def test_construction_offline_and_two_fixed_endpoints():
    transport = Transport()
    token = provider(transport)
    assert not transport.calls
    assert token() == TOKEN
    assert len(transport.calls) == 2
    first, second = transport.calls
    assert first[0] == "GET" and first[1] == I.oidc_url(URL)
    assert first[2]["headers"]["Authorization"] == "Bearer " + JOB_TOKEN
    assert "audience=api%3A%2F%2FAzureADTokenExchange" in first[1]
    assert second[0:2] == ("POST", f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")
    assert "Authorization" not in second[2]["headers"]
    assert second[2]["data"] == {"client_id": CLIENT, "scope": I.SCOPE,
        "grant_type": "client_credentials", "client_assertion": ASSERTION,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"}
    assert all(r.closed for r in transport.responses)
    assert not any(secret in repr(token) for secret in (TOKEN, JOB_TOKEN, ASSERTION))


@pytest.mark.parametrize("url", [
    URL.replace("https:", "http:"), URL.replace("vstoken.", "vstoken.evil."),
    URL.replace(".com/", ".com.evil/"), URL.replace("https://", "https://user:pass@"),
    URL.replace(".com/", ".com:443/"), URL.replace(".com/", ".com./"),
    URL + "#", URL + "#fragment", URL + "&audience=other", URL + "&x=1",
    URL.replace("2.0", "1.0"), URL.replace("/_apis/", "/../_apis/"),
    URL.replace("/_apis/", "/%2e%2e/_apis/"), URL + "\n", " " + URL,
    URL.replace("vstoken.actions.githubusercontent.com", "127.0.0.1"),
    "https://actions.githubusercontent.com/_apis/oidc/token?api-version=2.0", None,
])
def test_untrusted_or_modified_endpoint_rejected_before_token_read(url):
    with pytest.raises(StateError, match="invalid_oidc_endpoint"):
        I.GitHubStorageToken(TENANT, CLIENT, url, request_token_provider=lambda: pytest.fail("No token read"))


@pytest.mark.parametrize("value", ["common", "../other", "", None, "0" * 36])
def test_tenant_and_client_require_guids(value):
    for tenant, client in ((value, CLIENT), (TENANT, value)):
        with pytest.raises(StateError, match="invalid_federation_settings"):
            I.GitHubStorageToken(tenant, client, URL)


def test_cached_token_refresh_margin_and_concurrent_readers():
    now, transport = [1000], Transport()
    token = provider(transport, clock=lambda: now[0])
    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(lambda _: token(), range(20))) == [TOKEN] * 20
    assert len(transport.calls) == 2
    now[0] = 1000 + 3600 - I.MARGIN - 1
    assert token() == TOKEN and len(transport.calls) == 2
    now[0] += 1
    assert token() == TOKEN and len(transport.calls) == 4


def test_failed_refresh_never_returns_old_token():
    now, transport = [1000], Transport()
    token = provider(transport, clock=lambda: now[0])
    token()
    now[0] += 3600 - I.MARGIN
    transport.response = Response(503, body=TOKEN.encode())
    with pytest.raises(StateError, match="identity_request_failed") as exc:
        token()
    assert TOKEN not in str(exc.value) and token._cached is None
    assert len(transport.calls) == 3  # No automatic retry or exchange after a failed GET.


@pytest.mark.parametrize("extra", [
    {"expires_in": True}, {"expires_in": "3600"}, {"expires_in": 0}, {"expires_in": 120},
    {"expires_in": 86401}, {"access_token": "short"}, {"access_token": TOKEN + "\r\nInjected: yes"},
    {"token_type": "Basic"}, {"token_type": None}, {"error": "sensitive failure"},
])
def test_invalid_exchange_response_not_cached(extra):
    transport = Transport()
    transport.extra = extra
    token = provider(transport)
    with pytest.raises(StateError):
        token()
    assert token._cached is None
    assert all(r.closed for r in transport.responses)


@pytest.mark.parametrize("response", [
    Response(302, headers={"Location": "https://evil.example"}),
    Response(body=b'{"value":"one","value":"two"}', headers={"Content-Type": "application/json"}),
    Response(body=b"[]", headers={"Content-Type": "application/json"}),
    Response(body=b"x" * (I.MAX_RESPONSE + 1), headers={"Content-Type": "application/json"}),
    Response(body=b"{}", headers={"Content-Type": "text/html"}),
    Response(body=b"{}", headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}),
    Response(body=b'{"value":"invalid-assertion"}', headers={"Content-Type": "application/json"}),
])
def test_bad_response_stops_before_exchange(response):
    transport = Transport()
    transport.response = response
    with pytest.raises(StateError):
        provider(transport)()
    assert len(transport.calls) == 1 and response.closed


def test_latency_and_clock_rollback_do_not_extend_validity():
    times = iter([1000, 4480])
    with pytest.raises(StateError, match="storage_token_too_old"):
        provider(clock=lambda: next(times))()
    now, transport = [1000], Transport()
    token = provider(transport, clock=lambda: now[0])
    token()
    now[0] = 999
    token()
    assert len(transport.calls) == 4


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1, True, None])
def test_invalid_clock_has_no_requests(now):
    transport = Transport()
    with pytest.raises(StateError, match="invalid_identity_clock"):
        provider(transport, clock=lambda: now)()
    assert not transport.calls


def test_blob_adapter_uses_refreshed_tokens_without_changes():
    transport, backend, now = Transport(), Backend(), [1000]
    token = provider(transport, clock=lambda: now[0])
    store = A.AzureBlobStore("fxtest123", "private-fx", token_provider=token, transport=backend)
    store.compare_and_swap("inputs/test", None, b"private synthetic state")
    now[0] += 3600
    assert store.read("inputs/test").body == b"private synthetic state"
    assert len(transport.calls) == 4


def test_default_job_token_missing_has_no_credential_fallback(monkeypatch):
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.setenv("FXDASH_BLOB_ACCESS_TOKEN", TOKEN)
    transport = Transport()
    token = I.GitHubStorageToken(TENANT, CLIENT, URL, transport=transport)
    with pytest.raises(StateError, match="oidc_job_token_required"):
        token()
    assert not transport.calls


def test_session_ignores_ambient_credentials(monkeypatch):
    transport = Transport()
    transport.trust_env = True
    monkeypatch.setattr("requests.Session", lambda: transport)
    token = I.GitHubStorageToken(TENANT, CLIENT, URL, request_token_provider=lambda: JOB_TOKEN)
    assert token() == TOKEN and transport.trust_env is False
