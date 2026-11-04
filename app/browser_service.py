"""Internal HTTP service that owns the sandboxed Chromium process."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.browser_renderer import render_browser_page_local


MAX_CONCURRENT = max(1, int(os.getenv("BROWSER_MAX_CONCURRENT", "2")))
_capacity = asyncio.Semaphore(MAX_CONCURRENT)
app = FastAPI(title="AgentSearch Browser Worker", docs_url=None, redoc_url=None)


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
