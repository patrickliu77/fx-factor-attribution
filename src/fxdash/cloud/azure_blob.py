"""Azure Blob REST adapter. No provisioning, deletion, listing or implicit retry.

Only a private container on the primary public-Azure endpoint is supported.
Tokens are supplied in memory and sent in headers, never URLs. Construction is
offline; reads and conditional writes are explicit operations.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import os
import re

from .state import Conflict, MAX_BYTES, Record, StateError, valid_key

API_VERSION = "2023-11-03"
PREFIX = "fxdash-v1/"


def etag(value):
    if not isinstance(value, str) or not re.fullmatch(r'"[A-Za-z0-9_-]{1,128}"', value):
        raise StateError("invalid_blob_etag")
    return value


def md5(body):
    # Transport corruption check, not an authenticity or security primitive.
    return base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()


def access_token():
    return os.environ.get("FXDASH_BLOB_ACCESS_TOKEN", "")


class AzureBlobStore:
    def __init__(self, account, container, *, token_provider=access_token, transport=None):
        if (not isinstance(account, str) or not re.fullmatch(r"[a-z0-9]{3,24}", account)
                or not isinstance(container, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", container)
                or "--" in container or not callable(token_provider)):
            raise StateError("invalid_blob_settings")
        self._url = f"https://{account}.blob.core.windows.net/{container}"
        self._token_provider, self._transport = token_provider, transport

    def _request(self, method, suffix, *, headers=None, data=None):
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]{20,16384}={0,2}", token):
                raise StateError("blob_token_required")
            if self._transport is None:
                import requests
                session = requests.Session()
                session.trust_env = False  # No ambient .netrc credentials or proxies.
                self._transport = session
            request_headers = {"Authorization": "Bearer " + token,
                               "x-ms-version": API_VERSION,
                               "x-ms-date": format_datetime(datetime.now(timezone.utc), usegmt=True),
                               "Accept-Encoding": "identity", "Cache-Control": "no-cache"}
            request_headers.update(headers or {})
            return self._transport.request(method, self._url + suffix, headers=request_headers,
                                           data=data, stream=True, timeout=(5, 30), allow_redirects=False)
        except StateError:
            raise
        except Exception:
            raise StateError("blob_transport_unavailable") from None

    def _private(self):
        response = self._request("HEAD", "?restype=container")
        try:
            if response.status_code != 200 or "x-ms-blob-public-access" in response.headers:
                raise StateError("private_container_unconfirmed")
        finally:
            response.close()

    def read(self, key):
        valid_key(key)
        self._private()
        response = self._request("GET", "/" + PREFIX + key)
        try:
            # An absent account/container, denied authorization or proxy error
            # must not look like an empty journal that can be bootstrapped.
            if response.status_code == 404 and response.headers.get("x-ms-error-code") == "BlobNotFound":
                return None
            if response.status_code != 200:
                raise StateError("blob_read_failed")
            version = etag(response.headers.get("ETag"))
            headers = response.headers
            if (headers.get("Content-Encoding") or headers.get("Content-Range")
                    or headers.get("x-ms-blob-type") != "BlockBlob"
                    or headers.get("x-ms-server-encrypted", "").lower() != "true"):
                raise StateError("invalid_blob_response")
            length = headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > MAX_BYTES:
                raise StateError("blob_size_limit")
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > int(length) or size > MAX_BYTES:
                    raise StateError("blob_size_limit")
                chunks.append(chunk)
            body = b"".join(chunks)
            if (size != int(length) or headers.get("x-ms-meta-fxdash_sha256") != hashlib.sha256(body).hexdigest()
                    or headers.get("Content-MD5") != md5(body)):
                raise StateError("blob_integrity_failed")
            return Record(version, body)
        except StateError:
            raise
        except Exception:
            raise StateError("blob_read_failed") from None
        finally:
            response.close()

    def compare_and_swap(self, key, expected, body):
        valid_key(key)
        if not isinstance(body, bytes) or len(body) > MAX_BYTES:
            raise StateError("invalid_state_write")
        condition = {"If-None-Match": "*"} if expected is None else {"If-Match": etag(expected)}
        headers = {**condition, "x-ms-blob-type": "BlockBlob", "Content-Type": "application/octet-stream",
                   "Content-Length": str(len(body)), "Content-MD5": md5(body),
                   "x-ms-meta-fxdash_sha256": hashlib.sha256(body).hexdigest(), "Cache-Control": "no-store"}
        self._private()
        response = self._request("PUT", "/" + PREFIX + key, headers=headers, data=body)
        try:
            if response.status_code == 412:
                raise Conflict("state_conflict")
            if response.status_code != 201:
                raise StateError("blob_write_unconfirmed")
            version = etag(response.headers.get("ETag"))
            if (response.headers.get("Content-MD5") != md5(body)
                    or response.headers.get("x-ms-request-server-encrypted", "").lower() != "true"):
                raise StateError("blob_write_unconfirmed")
            return Record(version, body)
        finally:
            response.close()
