"""Safe fetch helpers for bundled content adapters."""

from __future__ import annotations

from urllib.parse import urljoin

import httpx
import requests

from app.killchain import MAX_REDIRECTS, UnsafeRedirectError, is_safe_url


def safe_requests_get(
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    **kwargs,
) -> requests.Response:
    """requests.get with manual redirect validation."""
    current_url = url
    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)
    for _ in range(max_redirects + 1):
        if not is_safe_url(current_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe URL blocked: {current_url}")

        response = requests.get(current_url, allow_redirects=False, **request_kwargs)
        if not 300 <= response.status_code < 400:
            return response

        location = response.headers.get("Location")
        response.close()
        if not location:
            return response

        next_url = urljoin(current_url, location)
        if not is_safe_url(next_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe redirect blocked: {current_url} -> {next_url}")
        current_url = next_url

    raise UnsafeRedirectError(f"Too many redirects for URL: {url}")


async def safe_httpx_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """httpx.AsyncClient.get with manual redirect validation."""
    current_url = url
    request_kwargs = dict(kwargs)
    request_kwargs.pop("follow_redirects", None)
    for _ in range(max_redirects + 1):
        if not is_safe_url(current_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe URL blocked: {current_url}")

        response = await client.get(current_url, follow_redirects=False, **request_kwargs)
        if not response.is_redirect:
            return response

        location = response.headers.get("location")
        if not location:
            return response

        next_url = urljoin(str(response.url or current_url), location)
        if not is_safe_url(next_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe redirect blocked: {current_url} -> {next_url}")
        current_url = next_url

    raise UnsafeRedirectError(f"Too many redirects for URL: {url}")
