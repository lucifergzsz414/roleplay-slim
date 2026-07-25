"""OpenAI-compatible compression proxy.

Sits between a chat app and its real LLM provider. Only the request's
`messages` field is touched (via roleplay_slim.compress); everything else,
including the response — streamed or not — passes through untouched.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..compressor import compress
from ..config import ProxyConfig
from ..stats import CompressionStats


def create_app(config: ProxyConfig, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """transport lets tests substitute an httpx.MockTransport for the real
    network call, so the proxy's own request/response handling (headers,
    streaming passthrough, compression call-through) can be exercised
    without hitting a real upstream API."""
    app = FastAPI(title="roleplay-slim proxy")
    stats = CompressionStats()
    api_key = os.environ.get(config.upstream_api_key_env, "")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/stats")
    async def get_stats():
        return stats.summary()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages", [])
        compressed = compress(messages, config.compressor)
        stats.record(messages, compressed)
        body["messages"] = compressed

        headers = {"Content-Type": "application/json"}
        incoming_auth = request.headers.get("authorization")
        headers["Authorization"] = incoming_auth or (f"Bearer {api_key}" if api_key else "")

        upstream_url = f"{config.upstream_base_url.rstrip('/')}/chat/completions"
        is_streaming = bool(body.get("stream"))

        if not is_streaming:
            async with httpx.AsyncClient(timeout=120.0, transport=transport) as client:
                resp = await client.post(upstream_url, json=body, headers=headers)
            return JSONResponse(content=resp.json(), status_code=resp.status_code)

        async def stream_upstream() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=120.0, transport=transport) as client:
                async with client.stream("POST", upstream_url, json=body, headers=headers) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(stream_upstream(), media_type="text/event-stream")

    return app
