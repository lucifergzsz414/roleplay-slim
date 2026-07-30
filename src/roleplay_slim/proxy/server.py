"""OpenAI-compatible compression proxy.

Sits between a chat app and its real LLM provider. The request's `messages`
field is compressed via roleplay_slim.compress; the rest of the request body
is forwarded as-is. Response bodies (JSON, HTML, plain text — whatever the
upstream actually returns) pass through without being parsed and re-serialized
so non-JSON error pages from CDNs and gateways don't cause a proxy-side 500.
"""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..compressor import compress
from ..config import ProxyConfig
from ..stats import CompressionStats

logger = logging.getLogger("roleplay_slim")

# Headers that must never be forwarded, per RFC 2616 §13.5.1 — they belong
# to a single hop (the proxy's own connection from/to its peer), not to the
# end-to-end request/response.
_HOP_BY_HOP_HEADERS = frozenset({
    "host", "content-length", "connection", "transfer-encoding",
    "keep-alive", "upgrade", "proxy-authenticate", "proxy-authorization",
    "te", "trailers",
})


def _bearer_token(auth_header: str | None) -> str:
    """Extract the token portion of a Bearer auth header.

    A header that's present but carries no real token (e.g. some client
    apps always send ``Authorization: Bearer `` with nothing after it when
    their own API key setting is empty) means "no real credential" just as
    much as a missing header does — callers should fall back to the
    proxy's configured upstream key rather than forward a credential-less
    header that would just 401 upstream.
    """
    if not auth_header:
        return ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[len("Bearer "):].strip()
    return auth_header.strip()


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
            if not client_auth_token or not secrets.compare_digest(
                incoming_auth or "", expected or ""
            ):
                return JSONResponse(
                    {"error": {"message": "invalid or missing proxy credentials"}},
                    status_code=401,
                )
            # This header authenticated access to the proxy, not to the
            # upstream provider — don't forward it; always use the proxy's
            # own configured upstream key instead.
            incoming_auth = None

        # Validate the request body before touching anything else — a
        # malformed request should get a clear 400, not an internal 500
        # from somewhere deep in the compression or upstream call.
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"message": "request body must be valid JSON"}},
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": {"message": "request body must be a JSON object"}},
                status_code=400,
            )
        messages = body.get("messages")
        if not isinstance(messages, list):
            return JSONResponse(
                {"error": {"message": "messages must be an array"}},
                status_code=400,
            )
        if messages and not all(isinstance(m, dict) for m in messages):
            return JSONResponse(
                {"error": {"message": "every message must be an object"}},
                status_code=400,
            )

        try:
            compressed = compress(messages, config.compressor)
        except Exception:
            logger.exception("compression failed for request #%d", stats.request_count + 1)
            return JSONResponse(
                {"error": {"message": "internal error during compression"}},
                status_code=500,
            )

        entry = stats.record(messages, compressed)
        pct = (entry["saved"] / entry["tokens_before"] * 100) if entry["tokens_before"] else 0.0

        # Diagnostic: show message structure so we can tell why compression
        # rate is low (small prefix? few turns? already-under-window?).
        from ..segmenter import segment

        prefix, turns = segment(messages)
        tcounts = [len(t.messages) for t in turns]
        logger.info(
            "request #%d | %d -> %d tokens (saved %d, %.1f%%) | msgs:%d pre:%d turns:%d%s",
            stats.request_count,
            entry["tokens_before"],
            entry["tokens_after"],
            entry["saved"],
            pct,
            len(messages),
            len(prefix),
            len(turns),
            f" tcounts:{tcounts}" if tcounts else "",
        )
        body["messages"] = compressed

        # Forward every request header that isn't hop-by-hop, then
        # overwrite the two headers we control: Content-Type (always JSON
        # for an OpenAI-compatible request) and Authorization (the real
        # upstream key, unless the caller supplied their own).
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        headers["content-type"] = "application/json"
        # A caller-supplied Authorization header only wins if it actually
        # carries a token — some client apps send a bare "Bearer " with
        # nothing after it when their own API key setting is empty, which
        # is not a real credential and would just 401 upstream if forwarded
        # as-is.
        headers["authorization"] = (
            incoming_auth if _bearer_token(incoming_auth)
            else (f"Bearer {api_key}" if api_key else "")
        )

        upstream_url = f"{config.upstream_base_url.rstrip('/')}/chat/completions"
        # Forward query-string parameters (e.g. ?customer_id=...) so the
        # proxy is transparent to anything the caller appended to its URL.
        qs = request.url.query
        if qs:
            upstream_url = f"{upstream_url}?{qs}"

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
            # Pass through the raw upstream response — JSON, HTML error
            # page, plain text, or empty body — rather than assuming JSON
            # and crashing on the first CDN/gateway error page that isn't.
            resp_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in _HOP_BY_HOP_HEADERS
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
                media_type=resp.headers.get("content-type"),
            )

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

        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        return StreamingResponse(
            stream_upstream(),
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type", "text/event-stream"),
        )

    return app
