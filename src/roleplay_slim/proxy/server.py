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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..compressor import compress
from ..config import ProxyConfig
from .stats_store import StatsStore

logger = logging.getLogger("roleplay_slim")

# Headers that must never be forwarded, per RFC 2616 §13.5.1 — they belong
# to a single hop (the proxy's own connection from/to its peer), not to the
# end-to-end request/response.
#
# content-encoding is included for a different reason: httpx transparently
# decompresses the upstream response body (gzip/br/deflate) before we ever
# see resp.content / resp.aiter_bytes(), but resp.headers still carries the
# original encoding the upstream server sent. Forwarding that stale header
# alongside an already-decoded body lies to the client about the encoding —
# e.g. UnityWebRequest doesn't support brotli and fails with
# "Unrecognized content-encoding" even though the bytes it received are
# plain text.
_HOP_BY_HOP_HEADERS = frozenset({
    "host", "content-length", "connection", "transfer-encoding",
    "keep-alive", "upgrade", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "content-encoding",
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


def _check_client_auth(
    incoming_auth: str | None, config: ProxyConfig, client_auth_token: str
) -> tuple[JSONResponse | None, str | None]:
    """Gate access to the proxy itself (distinct from the upstream
    provider's auth).

    Returns ``(error_response, forwardable_auth)``. A non-None first
    element means the caller must return it immediately. Otherwise the
    second element is the Authorization header that may still be
    forwarded upstream — None once proxy-level auth has consumed it,
    since a header that authenticated access *to the proxy* is not an
    upstream credential and must not be passed along.

    Shared by every route that spends the upstream API key. A route that
    skipped this would let anyone who can reach the proxy's port bill
    calls to the operator's real provider account.
    """
    if not config.client_auth_token_env and not config.client_auth_tokens_extra:
        return None, incoming_auth

    allowed = _allowed_client_tokens(config, client_auth_token)
    if not any(secrets.compare_digest(incoming_auth or "", a) for a in allowed):
        return (
            JSONResponse(
                {"error": {"message": "invalid or missing proxy credentials"}},
                status_code=401,
            ),
            None,
        )
    return None, None


def _allowed_client_tokens(config: ProxyConfig, client_auth_token: str) -> set[str]:
    """The complete set of client credentials this proxy accepts.

    The single configured ``client_auth_token_env`` value plus every entry
    of the comma-separated ``client_auth_tokens_extra`` list (whitespace
    trimmed, empties dropped). This is what lets more than one caller — e.g.
    a phone app and a chat bot — authenticate against one proxy instance.
    """
    allowed: set[str] = set()
    if client_auth_token:
        allowed.add(f"Bearer {client_auth_token}")
    allowed.update(
        f"Bearer {t.strip()}"
        for t in config.client_auth_tokens_extra.split(",")
        if t.strip()
    )
    return allowed


def _build_upstream_headers(
    request: Request, incoming_auth: str | None, api_key: str
) -> dict[str, str]:
    """Forward every request header that isn't hop-by-hop, then overwrite
    the two headers the proxy controls: Content-Type and Authorization.

    A caller-supplied Authorization header only wins if it actually
    carries a token — some client apps send a bare "Bearer " with nothing
    after it when their own API key setting is empty, which is not a real
    credential and would just 401 upstream if forwarded as-is.
    """
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS
    }
    headers["content-type"] = "application/json"
    headers["authorization"] = (
        incoming_auth
        if incoming_auth is not None and _bearer_token(incoming_auth)
        else (f"Bearer {api_key}" if api_key else "")
    )
    return headers


def _passthrough_response(resp: httpx.Response) -> Response:
    """Return an upstream response verbatim — JSON, HTML error page, plain
    text, or empty body — rather than assuming JSON and crashing on the
    first CDN/gateway error page that isn't."""
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={
            k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS
        },
        media_type=resp.headers.get("content-type"),
    )


def _record_upstream_usage(stats: StatsStore, resp: httpx.Response) -> None:
    """Pull the provider-reported `usage` block out of a completed upstream
    response and hand it to stats.

    Why this matters: everything else in /stats is an *estimate* produced
    by running an OpenAI tokenizer over text destined for some other
    provider. The response body already carries the provider's own
    accounting — including, on DeepSeek, prompt_cache_hit_tokens, which is
    the only direct evidence that leaving the prefix byte-identical
    actually preserves the upstream prefix cache. That's this project's
    central claim, so it should be measured rather than asserted.

    Strictly best-effort and side-effect-free with respect to the response:
    the body is already fully in memory (a non-streaming httpx response),
    so reading it here does not disturb the passthrough that follows. Any
    failure is swallowed — telemetry must never turn a successful upstream
    call into a proxy error.
    """
    if resp.status_code != 200:
        return
    if "json" not in resp.headers.get("content-type", "").lower():
        return
    try:
        payload = resp.json()
    except Exception:
        # A 200 that claims JSON but isn't parseable is the upstream's
        # problem, not ours — the raw bytes still get passed through to
        # the caller untouched.
        return
    if not isinstance(payload, dict):
        return
    try:
        recorded = stats.record_usage(payload.get("usage"))
    except Exception:  # pragma: no cover - record_usage is already total
        logger.exception("failed to record upstream usage")
        return
    if recorded:
        logger.info(
            "upstream usage | prompt:%s completion:%s cache_hit:%s cache_miss:%s",
            recorded["prompt_tokens"],
            recorded["completion_tokens"],
            recorded["prompt_cache_hit_tokens"],
            recorded["prompt_cache_miss_tokens"],
        )


def create_app(config: ProxyConfig, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """transport lets tests substitute an httpx.MockTransport for the real
    network call, so the proxy's own request/response handling (headers,
    streaming passthrough, compression call-through) can be exercised
    without hitting a real upstream API."""
    # /stats source of truth. persist=true → SQLite file that survives
    # restarts; persist=false → the same store over ":memory:" (identical
    # output, nothing written to disk).
    stats = StatsStore(":memory:" if not config.stats.persist else config.stats.db_path)
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
            stats.close()

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
        auth_error, incoming_auth = _check_client_auth(incoming_auth, config, client_auth_token)
        if auth_error is not None:
            return auth_error

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

        headers = _build_upstream_headers(request, incoming_auth, api_key)

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
            _record_upstream_usage(stats, resp)
            return _passthrough_response(resp)

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

    # Registered *after* /v1/chat/completions so the explicit route keeps
    # winning — Starlette matches routes in registration order, and a
    # catch-all that shadowed the compression endpoint would silently turn
    # this whole project into a plain forwarding proxy.
    #
    # Exists because real clients call more than one endpoint: SillyTavern,
    # OpenWebUI and friends fetch GET /v1/models on connect to populate
    # their model picker, and a 404 there reads as "this server is broken"
    # before the user ever gets to send a message. Nothing here is
    # compressed — these endpoints carry no `messages` array.
    @app.api_route(
        "/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def passthrough(path: str, request: Request):
        incoming_auth = request.headers.get("authorization")
        auth_error, incoming_auth = _check_client_auth(incoming_auth, config, client_auth_token)
        if auth_error is not None:
            return auth_error

        upstream_url = f"{config.upstream_base_url.rstrip('/')}/{path}"
        qs = request.url.query
        if qs:
            upstream_url = f"{upstream_url}?{qs}"

        headers = _build_upstream_headers(request, incoming_auth, api_key)
        raw_body = await request.body()
        # Content-Type is forced to JSON by _build_upstream_headers for the
        # benefit of the chat endpoint; a bodyless GET must not claim to
        # carry a JSON body.
        if not raw_body:
            headers.pop("content-type", None)

        client: httpx.AsyncClient = request.app.state.client
        try:
            resp = await client.request(
                request.method, upstream_url, content=raw_body or None, headers=headers
            )
        except httpx.HTTPError as e:
            logger.warning("upstream passthrough request failed: %s", e)
            return JSONResponse(
                {"error": {"message": f"upstream request failed: {e}"}}, status_code=502
            )

        logger.info("passthrough %s /v1/%s -> %d", request.method, path, resp.status_code)
        return _passthrough_response(resp)

    return app
