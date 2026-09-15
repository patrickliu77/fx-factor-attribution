import hashlib
import json
import threading

import pytest
from requests.structures import CaseInsensitiveDict

from fxdash.cloud import azure_blob as A, journal as J
from fxdash.cloud.state import StateError, Conflict, read_artifact, save_artifact

TOKEN = "synthetic-offline-bearer-token"
BASE = "https://fxtest123.blob.core.windows.net/private-fx"


class Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.body = status, body
        self.headers = CaseInsensitiveDict(headers or {})
        self.closed, self.iterated = False, False

    def iter_content(self, size):
        self.iterated = True
        yield self.body

    def close(self):
        self.closed = True


class Backend:
    def __init__(self):
        self.rows, self.calls, self.counter = {}, [], 0
        self.lock = threading.Lock()
        self.public, self.next_response, self.fail_once_state = None, None, None

    def request(self, method, url, **kwargs):
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["timeout"] == (5, 30)
        assert kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
        assert TOKEN not in url
        self.calls.append((method, url, kwargs))
        if method == "HEAD":
            assert url == BASE + "?restype=container"
            return Response(headers={"x-ms-blob-public-access": self.public} if self.public is not None else {})
        assert url.startswith(BASE + "/" + A.PREFIX)
        if self.next_response:
            response, self.next_response = self.next_response, None
            return response
        key = url.removeprefix(BASE + "/" + A.PREFIX)
        with self.lock:
            if method == "GET":
                if key not in self.rows:
                    return Response(404, headers={"x-ms-error-code": "BlobNotFound"})
                version, body = self.rows[key]
                return Response(body=body, headers={"ETag": version, "Content-Length": str(len(body)),
                    "x-ms-blob-type": "BlockBlob", "x-ms-server-encrypted": "true",
                    "Content-MD5": A.md5(body), "x-ms-meta-fxdash_sha256": hashlib.sha256(body).hexdigest()})
            assert method == "PUT"
            old = self.rows.get(key)
            headers, body = kwargs["headers"], kwargs["data"]
            assert ("If-Match" in headers) != ("If-None-Match" in headers)
            assert headers["Content-MD5"] == A.md5(body)
            assert headers["x-ms-meta-fxdash_sha256"] == hashlib.sha256(body).hexdigest()
            if (headers.get("If-None-Match") == "*" and old is not None
                    or "If-Match" in headers and (old is None or old[0] != headers["If-Match"])):
                return Response(412)
            self.counter += 1
            version = f'"0x{self.counter:X}"'
            self.rows[key] = (version, body)
            if key == J.KEY and self.fail_once_state:
                value = json.loads(body)
                if any(r["state"] == self.fail_once_state for r in value["claims"].values()):
                    self.fail_once_state = None
                    raise TimeoutError("private provider error after committed write")
            return Response(201, headers={"ETag": version, "Content-MD5": A.md5(body),
                                         "x-ms-request-server-encrypted": "true"})


def store(backend=None):
    return A.AzureBlobStore("fxtest123", "private-fx", token_provider=lambda: TOKEN, transport=backend or Backend())


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("No real Azure request"))


def test_constructing_adapter_is_offline_and_never_fetches_a_token():
    A.AzureBlobStore("fxtest123", "private-fx", token_provider=lambda: pytest.fail("constructor is offline"))


@pytest.mark.parametrize("account,container", [
    ("https://evil.test", "private-fx"), ("account-secondary", "private-fx"), ("UPPER", "private-fx"),
    ("ab", "private-fx"), ("a" * 25, "private-fx"), ("fxtest123", "$root"),
    ("fxtest123", "a--b"), ("fxtest123", "-bad"), ("fxtest123", "bad-"),
    ("fxtest123", "a/b"), ("fxtest123", "private?sig=secret"), ("fxtest123", "a" * 64),
])
def test_only_primary_azure_account_and_strict_container_names_are_allowed(account, container):
    with pytest.raises(StateError, match="invalid_blob_settings"):
        A.AzureBlobStore(account, container)


def test_private_container_round_trip_and_conditional_conflicts():
    backend = Backend()
    one, two = store(backend), store(backend)
    assert one.read("test/value") is None
    first = one.compare_and_swap("test/value", None, b"one")
    assert two.read("test/value") == first
    with pytest.raises(Conflict):
        two.compare_and_swap("test/value", None, b"again")
    second = two.compare_and_swap("test/value", first.version, b"two")
    with pytest.raises(Conflict):
        one.compare_and_swap("test/value", first.version, b"stale")
    assert one.read("test/value") == second
    assert all(url.startswith(BASE) and method in {"GET", "HEAD", "PUT"} for method, url, _ in backend.calls)


@pytest.mark.parametrize("access", ["blob", "container", "", "unexpected"])
def test_public_or_unknown_container_access_blocks_both_read_and_write(access):
    backend = Backend()
    backend.public = access
    for action in (lambda: store(backend).read("test/a"), lambda: store(backend).compare_and_swap("test/a", None, b"private")):
        with pytest.raises(StateError, match="private_container_unconfirmed"):
            action()
    assert [method for method, _, _ in backend.calls] == ["HEAD", "HEAD"]


@pytest.mark.parametrize("status,error", [(404, "ContainerNotFound"), (404, None), (401, None),
    (403, None), (301, None), (307, None), (429, None), (500, None)])
def test_failed_reads_never_look_like_an_empty_store_or_follow_redirects(status, error):
    backend = Backend()
    response = Response(status, b"private response", {"x-ms-error-code": error, "Location": "https://evil.test"})
    backend.next_response = response
    with pytest.raises(StateError, match="blob_read_failed"):
        store(backend).read("test/value")
    assert response.closed and not response.iterated and len(backend.calls) == 2


@pytest.mark.parametrize("expected", ["*", "0x123", 'W/"0x123"', '"a"\r\nx:secret', 7, '"a", "b"'])
def test_bad_etags_are_rejected_before_any_network_request(expected):
    backend = Backend()
    with pytest.raises(StateError, match="invalid_blob_etag"):
        store(backend).compare_and_swap("test/value", expected, b"body")
    assert backend.calls == []


@pytest.mark.parametrize("change", [
    {"ETag": "*"}, {"Content-Length": "9999999999"}, {"Content-Length": "1"},
    {"Content-Length": "wrong"}, {"Content-MD5": "wrong"}, {"x-ms-meta-fxdash_sha256": "wrong"},
    {"x-ms-server-encrypted": "false"}, {"Content-Encoding": "gzip"},
    {"Content-Range": "bytes 0-3/4"}, {"x-ms-blob-type": "AppendBlob"},
])
def test_corrupt_or_partial_blob_reads_fail_closed(change):
    backend = Backend()
    s = store(backend)
    s.compare_and_swap("test/value", None, b"body")
    response = backend.request("GET", BASE + "/" + A.PREFIX + "test/value",
        headers={"Authorization": "Bearer " + TOKEN}, allow_redirects=False, stream=True, timeout=(5, 30))
    response.headers.update(change)
    backend.next_response = response
    with pytest.raises(StateError):
        s.read("test/value")
    assert response.closed


@pytest.mark.parametrize("token", ["", "*", "short", "token with spaces" * 4, "a" * 20 + "\r\nx:bad"])
def test_invalid_tokens_are_not_sent_or_disclosed(token):
    backend = Backend()
    s = A.AzureBlobStore("fxtest123", "private-fx", token_provider=lambda: token, transport=backend)
    with pytest.raises(StateError, match="blob_token_required"):
        s.read("test/value")
    assert backend.calls == []


@pytest.mark.parametrize("status", [200, 202, 301, 403, 409, 429, 503])
def test_unconfirmed_writes_have_no_automatic_retry(status):
    backend = Backend()
    backend.next_response = Response(status, b"private provider message")
    with pytest.raises(StateError, match="blob_write_unconfirmed"):
        store(backend).compare_and_swap("test/value", None, b"body")
    assert len(backend.calls) == 2


def test_journal_and_immutable_artifacts_work_through_azure_protocol():
    backend = Backend()
    s = store(backend)
    J.Journal.initialize(s)
    body = b"persisted result"
    with J.Journal(s).session() as journal:
        receipt = journal.run_once("recap", "2026-01-08", "edition", hashlib.sha256(b"inputs").hexdigest(),
            lambda: {"artifact_sha256": save_artifact(s, body)})
    with J.Journal(store(backend)).session() as journal:
        assert journal.run_once("recap", "2026-01-08", "edition", hashlib.sha256(b"inputs").hexdigest(),
            lambda: pytest.fail("No second model call")) == receipt
        assert read_artifact(store(backend), receipt["artifact_sha256"]) == body


@pytest.mark.parametrize("phase", ["pending", "succeeded"])
def test_committed_write_with_lost_response_never_repeats_effect(phase):
    backend = Backend()
    s = store(backend)
    J.Journal.initialize(s)
    backend.fail_once_state = phase
    calls = []
    fingerprint = hashlib.sha256(b"inputs").hexdigest()
    with pytest.raises(StateError, match="blob_transport_unavailable"):
        with J.Journal(s).session() as journal:
            journal.run_once("recap", "2026-01-08", "edition", fingerprint,
                lambda: calls.append(1) or {"artifact_sha256": hashlib.sha256(b"result").hexdigest()})
    assert len(calls) == (1 if phase == "succeeded" else 0)
    with J.Journal(store(backend)).session() as journal:
        if phase == "pending":
            with pytest.raises(StateError, match="operation_needs_review"):
                journal.run_once("recap", "2026-01-08", "edition", fingerprint, lambda: pytest.fail("uncertain claim"))
        else:
            assert journal.run_once("recap", "2026-01-08", "edition", fingerprint,
                lambda: pytest.fail("already completed"))["artifact_sha256"]
