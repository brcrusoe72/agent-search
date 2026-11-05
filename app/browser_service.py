"""Internal HTTP service that owns the sandboxed Chromium process."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.browser_renderer import BrowserRenderResult, render_browser_page_local
from app.browser_search import search_url


MAX_CONCURRENT = max(1, int(os.getenv("BROWSER_MAX_CONCURRENT", "2")))
_capacity = asyncio.Semaphore(MAX_CONCURRENT)
app = FastAPI(title="AgentSearch Browser Worker", docs_url=None, redoc_url=None)
_search_cooldowns: dict[str, float] = {}


class RenderRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    max_chars: int | None = Field(None, ge=200, le=50000)
    max_links: int | None = Field(None, ge=0, le=200)
    timeout_ms: int | None = Field(None, ge=1000, le=60000)


@app.get("/live")
async def live() -> dict:
    return {"status": "alive", "max_concurrent": MAX_CONCURRENT}


@app.post("/render")
async def render(request: RenderRequest) -> dict:
    async with _capacity:
        result = await render_browser_page_local(
            request.url,
            max_chars=request.max_chars,
            max_links=request.max_links,
            timeout_ms=request.timeout_ms,
        )
    data = asdict(result)
    return data


class SearchRequest(BaseModel):
    provider: Literal["duckduckgo", "bing"]
    query: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    count: int = Field(default=10, ge=1, le=20)


@app.post("/search")
async def search(request: SearchRequest) -> dict:
    url = search_url(request.provider, request.query)
    if _search_cooldowns.get(request.provider, 0) > time.monotonic():
        return asdict(BrowserRenderResult(url=url, error="Browser provider cooling down after a failure",
                                          blocked_reason="cooldown"))

    async def attempt():
        async with _capacity:
            # A queued request may have waited while another hit a challenge.
            if _search_cooldowns.get(request.provider, 0) > time.monotonic():
                return BrowserRenderResult(url=url, error="Browser provider cooling down after a failure",
                                           blocked_reason="cooldown")
            return await render_browser_page_local(url, max_links=request.count, max_chars=2000,
                                                   timeout_ms=12000, _search_provider=request.provider)
    try:
        result = await asyncio.wait_for(attempt(), timeout=18)
    except TimeoutError:
        result = BrowserRenderResult(url=url, error="Browser discovery timed out", blocked_reason="timeout")
    if not result.success and result.blocked_reason not in {"cooldown", "no_results"}:
        # Bounded state (two fixed providers); no repeated hammering on blocks.
        delay = 300 if result.challenge_detected or result.blocked_reason == "http_error" else 30
        _search_cooldowns[request.provider] = time.monotonic() + delay
    return asdict(result)
