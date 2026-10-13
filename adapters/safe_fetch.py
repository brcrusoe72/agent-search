"""Adapter HTTP requests use pinned connections or an explicit validating proxy."""
from __future__ import annotations

import os
from urllib.parse import urljoin

import httpx
import requests

from app.fetch_security import (
    DEFAULT_MAX_RESPONSE_BYTES, ResponseTooLarge, SafeAsyncHTTPTransport,
    SafeSyncHTTPTransport, safe_httpx_get as _safe_httpx_get,
)
from app.killchain import MAX_REDIRECTS, UnsafeRedirectError, is_safe_url


def _client_options(*, asynchronous: bool) -> dict:
    # Docker explicitly supplies its validating egress gateway. Ignore ambient
    # proxy variables in native mode; no failure may fall back to direct access.
    proxy = os.getenv("FETCH_PROXY_URL")
    if proxy:
        return {"proxy": proxy, "trust_env": False}
    transport = SafeAsyncHTTPTransport if asynchronous else SafeSyncHTTPTransport
    return {"transport": transport(trust_env=False), "trust_env": False}


def _sync_client(**kwargs) -> httpx.Client:
    return httpx.Client(**_client_options(asynchronous=False), **kwargs)


def safe_requests_get(url: str, *, max_redirects: int = MAX_REDIRECTS,
                      max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, **kwargs) -> requests.Response:
    """Preserve the adapters' requests.Response interface with protected I/O."""
    kwargs.pop("allow_redirects", None)
    kwargs.pop("stream", None)
    timeout = kwargs.pop("timeout", 15)
    current = url
    with _sync_client(timeout=timeout) as client:
        for hop in range(max_redirects + 1):
            if not is_safe_url(current, verbose=True):
                raise UnsafeRedirectError(f"Unsafe URL blocked: {current}")
            with client.stream("GET", current, follow_redirects=False, **kwargs) as response:
                if response.has_redirect_location:
                    if hop == max_redirects:
                        raise UnsafeRedirectError("Too many redirects")
                    current = urljoin(str(response.url), response.headers["location"])
                    kwargs.pop("params", None)
                    continue
                length = response.headers.get("content-length")
                if length is not None and (int(length) < 0 or int(length) > max_bytes):
                    raise ResponseTooLarge("Invalid or excessive response length")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > max_bytes:
                        raise ResponseTooLarge("Response exceeded byte limit")
                    body.extend(chunk)
                result = requests.Response()
                result.status_code = response.status_code
                result.url = str(response.url)
                result.headers.update({k: v for k, v in response.headers.items()
                    if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}})
                result.encoding = response.encoding
                result._content = bytes(body)
                result._content_consumed = True
                return result
    raise UnsafeRedirectError("Too many redirects")


async def safe_httpx_get(client: httpx.AsyncClient, url: str, *,
                         max_redirects: int = MAX_REDIRECTS,
                         max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, **kwargs) -> httpx.Response:
    """Retain the caller's headers/timeout, never its unvalidated connection pool."""
    async with httpx.AsyncClient(headers=client.headers, timeout=client.timeout,
                                **_client_options(asynchronous=True)) as protected:
        return await _safe_httpx_get(protected, url,
            validator=lambda target: is_safe_url(target, verbose=True),
            max_redirects=max_redirects, max_bytes=max_bytes, **kwargs)
