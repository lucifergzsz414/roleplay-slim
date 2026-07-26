"""OpenAI-compatible compression proxy.

Sits between a chat app and its real LLM provider. Only the request's
`messages` field is touched (via roleplay_slim.compress); everything else,
including the response — streamed or not — passes through untouched.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..compressor import compress
from ..config import ProxyConfig
from ..stats import CompressionStats

logger = logging.getLogger("roleplay_slim")


def create_app(config: ProxyConfig, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """transport lets tests substitute an httpx.MockTransport for the real
    network call, so the proxy's own request/response handling (headers,
    streaming passthrough, compression call-through) can be exercised
    without hitting a real upstream API."""
    stats = CompressionStats()
    api_key = os.environ.get(config.upstream_api_key_env, "")
    if not api_key:
        logger.warning(
            "%s is not set and no environment fallback key is configured — "
            "requests that don't carry their own Authorization header will be "
            "sent upstream with no credentials and will likely get a 401",
            config.upstream_api_key_env,
        )

    client_auth_token = (
        os.environ.get(config.client_auth_token_env, "") if config.client_auth_token_env else ""
    )
    if config.client_auth_token_env and not client_auth_token:
        logger.warning(
            "client_auth_token_env is set to %r but that environment variable "
            "is empty — the proxy will accept requests from anyone who can "
            "reach it, since an empty required token can never be matched "
            "by a real client (every request will be rejected instead)",
            config.client_auth_token_env,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One client for the app's lifetime so requests reuse the
        # underlying connection pool instead of paying a fresh TCP/TLS
        # handshake every single call.
        async with httpx.AsyncClient(timeout=120.0, transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(title="roleplay-slim proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/stats")
    async def get_stats():
        return stats.summary()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        incoming_auth = request.headers.get("authorization")

        # Access control for the proxy itself, separate from the upstream
        # provider's own auth. Only enforced if the operator opted in via
        # client_auth_token_env — zero-config deployments are unaffected.
        if config.client_auth_token_env:
            expected = f"Bearer {client_auth_token}" if client_auth_token else None
            if not client_auth_token or incoming_auth != expected:
                return JSONResponse(
                    {"error": {"message": "invalid or missing proxy credentials"}},
                    status_code=401,
                )
            # This header authenticated access to the proxy, not to the
            # upstream provider — don't forward it; always use the proxy's
            # own configured upstream key instead.
            incoming_auth = None

        body = await request.json()
        messages = body.get("messages", [])
        compressed = compress(messages, config.compressor)
        entry = stats.record(messages, compressed)
        pct = (entry["saved"] / entry["tokens_before"] * 100) if entry["tokens_before"] else 0.0
        logger.info(
            "request #%d | %d -> %d tokens (saved %d, %.1f%%)",
            stats.request_count,
            entry["tokens_before"],
            entry["tokens_after"],
            entry["saved"],
            pct,
        )
        body["messages"] = compressed

        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = incoming_auth or (f"Bearer {api_key}" if api_key else "")

        upstream_url = f"{config.upstream_base_url.rstrip('/')}/chat/completions"
        is_streaming = bool(body.get("stream"))
        client: httpx.AsyncClient = request.app.state.client

        if not is_streaming:
            try:
                resp = await client.post(upstream_url, json=body, headers=headers)
            except httpx.HTTPError as e:
                logger.warning("upstream request failed: %s", e)
                return JSONResponse(
                    {"error": {"message": f"upstream request failed: {e}"}}, status_code=502
                )
            return JSONResponse(content=resp.json(), status_code=resp.status_code)

        # Open the upstream connection and read its status/headers before
        # committing to a StreamingResponse — once streaming has started,
        # the response's status code can no longer be changed, so a
        # connection failure needs to be caught here to return a real 502
        # instead of crashing mid-stream with an unhandled exception.
        upstream_request = client.build_request("POST", upstream_url, json=body, headers=headers)
        try:
            resp = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as e:
            logger.warning("upstream streaming request failed: %s", e)
            return JSONResponse(
                {"error": {"message": f"upstream request failed: {e}"}}, status_code=502
            )

        async def stream_upstream() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except httpx.HTTPError as e:
                logger.warning("upstream stream interrupted: %s", e)
            finally:
                await resp.aclose()

        return StreamingResponse(
            stream_upstream(),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "text/event-stream"),
        )

    return app
