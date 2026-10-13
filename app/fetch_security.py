"""Fail-closed URL validation, pinned DNS connections, and body limits."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from app.url_utils import hostname_matches, normalize_hostname


DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
Resolver = Callable[..., list[tuple]]


class UnsafeURLError(ValueError):
    """Raised when a URL or resolved destination violates the fetch policy."""


class ResponseTooLarge(ValueError):
    """Raised before an oversized response can be buffered or parsed."""


@dataclass(frozen=True)
class URLValidation:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _public_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_unspecified or "%" in value):
        raise UnsafeURLError(f"Non-global destination blocked: {address}")
    return address.compressed


def validate_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    blocked_domains: Iterable[str] = (),
    blocked_tlds: Iterable[str] = (),
) -> URLValidation:
    """Resolve and validate every destination address, failing closed (R4-R5)."""
    try:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise UnsafeURLError(f"Malformed URL: {url}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme or '<empty>'}")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("Userinfo in fetch URLs is not allowed")

    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURLError("Invalid hostname") from exc
    if not hostname:
        raise UnsafeURLError("URL has no valid hostname")
    for domain in blocked_domains:
        if hostname_matches(hostname, domain):
            raise UnsafeURLError(f"Blocked domain: {domain}")
    if any(hostname.endswith(str(tld).lower()) for tld in blocked_tlds):
        raise UnsafeURLError(f"Blocked hostname policy: {hostname}")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        resolve = resolver or socket.getaddrinfo
        try:
            rows = resolve(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            raise UnsafeURLError(f"DNS resolution failed for {hostname}") from exc
        addresses: list[str] = []
        for row in rows:
            try:
                address = _public_address(row[4][0])
            except (IndexError, TypeError, ValueError) as exc:
                if isinstance(exc, UnsafeURLError):
                    raise
                raise UnsafeURLError(f"Invalid DNS answer for {hostname}") from exc
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise UnsafeURLError(f"DNS resolution returned no addresses for {hostname}")
    else:
        addresses = [_public_address(literal.compressed)]

    return URLValidation(url=url, hostname=hostname, port=port, addresses=tuple(addresses))


def is_public_url(url: str, **kwargs) -> bool:
    try:
        validate_public_url(url, **kwargs)
        return True
    except (TypeError, ValueError):
        return False


class SafeAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, validate every answer, and connect to the validated IP (R6)."""

    def __init__(
        self,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
        resolver: Resolver | None = None,
        allowed_hostnames: Iterable[str] = (),
    ) -> None:
        from httpcore._backends.auto import AutoBackend

        self._backend = backend or AutoBackend()
        self._resolver = resolver
        self._allowed_hostnames = {
            normalize_hostname(hostname) for hostname in allowed_hostnames if hostname
        }

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        normalized = normalize_hostname(host)
        if normalized in self._allowed_hostnames:
            return await self._backend.connect_tcp(
                host,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        validation = validate_public_url(
            f"https://{_authority(host)}:{port}/",
            resolver=self._resolver,
        )
        last_error: Exception | None = None
        for address in validation.addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # try the next already-validated address
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
        raise UnsafeURLError("Unix-socket outbound fetches are not allowed")


def _authority(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


class SafeSyncNetworkBackend(httpcore.NetworkBackend):
    """Connect only to the numeric addresses returned by validation."""

    def __init__(self, *, backend=None, resolver=None):
        self._backend = backend or httpcore.SyncBackend()
        self._resolver = resolver

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        validation = validate_public_url(f"https://{_authority(host)}:{port}/", resolver=self._resolver)
        last_error = None
        for address in validation.addresses:
            try:
                return self._backend.connect_tcp(address, port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options)
            except (OSError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeURLError("Unix-socket outbound fetches are not allowed")


class SafeSyncHTTPTransport(httpx.HTTPTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pool._network_backend = SafeSyncNetworkBackend()


class SafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport whose direct TCP connections use validated DNS answers."""

    def __init__(self, *, allowed_hostnames: Iterable[str] = (), **kwargs) -> None:
        super().__init__(**kwargs)
        self._pool._network_backend = SafeAsyncNetworkBackend(
            allowed_hostnames=allowed_hostnames
        )


async def _limited_async_body(response: httpx.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ResponseTooLarge(
                    f"Response declares {content_length} bytes; limit is {max_bytes}"
                )
        except ValueError as exc:
            raise ResponseTooLarge("Invalid Content-Length header") from exc

    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ResponseTooLarge(f"Response exceeded {max_bytes} bytes")
    return bytes(body)


def _validator_allows(validator: Callable[[str], bool], url: str) -> None:
    try:
        allowed = validator(url)
    except Exception as exc:
        raise UnsafeURLError(f"URL validation failed: {url}") from exc
    if not allowed:
        raise UnsafeURLError(f"Unsafe URL blocked: {url}")


async def safe_httpx_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    validator: Callable[[str], bool] = is_public_url,
    max_redirects: int = 5,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    **kwargs,
) -> httpx.Response:
    """Stream a bounded response and validate every redirect before following it."""
    current_url = url
    request_kwargs = dict(kwargs)
    request_kwargs.pop("follow_redirects", None)
    for _ in range(max_redirects + 1):
        _validator_allows(validator, current_url)
        request = client.build_request("GET", current_url, **request_kwargs)
        streamed = await client.send(request, stream=True, follow_redirects=False)
        try:
            body = await _limited_async_body(streamed, max_bytes)
        finally:
            await streamed.aclose()
        response = httpx.Response(
            streamed.status_code,
            headers=[(k, v) for k, v in streamed.headers.multi_items()
                     if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}],
            content=body,
            request=request,
            extensions=streamed.extensions,
        )
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        next_url = urljoin(str(response.url), location)
        _validator_allows(validator, next_url)
        current_url = next_url
    raise UnsafeURLError(f"Too many redirects for URL: {url}")


def buffer_requests_response(response, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
    """Bound a streamed ``requests`` response before adapters access ``.text``."""
    content_length = response.headers.get("Content-Length") or response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ResponseTooLarge(
                    f"Response declares {content_length} bytes; limit is {max_bytes}"
                )
        except ValueError as exc:
            raise ResponseTooLarge("Invalid Content-Length header") from exc
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ResponseTooLarge(f"Response exceeded {max_bytes} bytes")
    response._content = bytes(body)
    response._content_consumed = True
    return response
