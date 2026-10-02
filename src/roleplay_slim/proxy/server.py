"""OpenAI-compatible compression proxy.

Sits between a chat app and its real LLM provider. The request's `messages`
field is compressed via roleplay_slim.compress; the rest of the request body
is forwarded as-is. Response bodies (JSON, HTML, plain text — whatever the
upstream actually returns) pass through without being parsed and re-serialized
so non-JSON error pages from CDNs and gateways don't cause a proxy-side 500.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..anthropic_proxy import (
    compress_anthropic_messages,
    estimate_anthropic_messages_chars,
)
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

_CONNECT_RETRY_DELAYS = (0.5, 1.5)
_RETRYABLE_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


async def _send_with_connect_retry(
    send: Callable[[], Awaitable[httpx.Response]],
) -> httpx.Response:
    """Retry failures that happen before an upstream connection is established.

    Read/write/stream errors are deliberately excluded: once request bytes may
    have reached the provider, retrying could duplicate billing or output.
    """
    for attempt, delay in enumerate(_CONNECT_RETRY_DELAYS, start=1):
        try:
            return await send()
        except _RETRYABLE_CONNECT_ERRORS as exc:
            logger.warning(
                "upstream connect attempt %d failed (%s); retrying in %.1fs",
                attempt,
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)
    return await send()


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


def _check_client_auth_anthropic(
    incoming_x_api_key: str | None, config: ProxyConfig, client_auth_token: str
) -> tuple[JSONResponse | None, str | None]:
    """Same purpose as `_check_client_auth`, kept as a separate function
    rather than a shared one: Anthropic clients authenticate to the proxy
    with a bare `x-api-key: <token>` value, not an `Authorization: Bearer
    <token>` header, so the credential-matching shape genuinely differs —
    reusing `_allowed_client_tokens`'s `"Bearer " + token` set here would
    require every caller to carry a header prefix Anthropic clients never
    send. Returns (error_response, forwardable_key) with the same contract
    as `_check_client_auth`.
    """
    if not config.client_auth_token_env and not config.client_auth_tokens_extra:
        return None, incoming_x_api_key

    allowed = _allowed_client_tokens_anthropic(config, client_auth_token)
    if not any(secrets.compare_digest(incoming_x_api_key or "", a) for a in allowed):
        return (
            JSONResponse(
                {"error": {"message": "invalid or missing proxy credentials"}},
                status_code=401,
            ),
            None,
        )
    return None, None


def _allowed_client_tokens_anthropic(config: ProxyConfig, client_auth_token: str) -> set[str]:
    """Anthropic-shape counterpart of `_allowed_client_tokens` — same token
    sources, no `"Bearer "` prefix since `x-api-key` carries the raw value."""
    allowed: set[str] = set()
    if client_auth_token:
        allowed.add(client_auth_token)
    allowed.update(
        t.strip()
        for t in config.client_auth_tokens_extra.split(",")
        if t.strip()
    )
    return allowed


def _build_anthropic_upstream_headers(
    request: Request, incoming_x_api_key: str | None, api_key: str
) -> dict[str, str]:
    """Anthropic-shape counterpart of `_build_upstream_headers`: forwards
    every non-hop-by-hop header, then sets the two headers this route
    controls — `x-api-key` (client's own value wins if present, same
    "caller credential wins" precedent as the OpenAI route) and
    `anthropic-version`, which Anthropic requires on every request and
    which this proxy does not try to guess a "current" value for — a client
    that cares which API version it's calling should say so explicitly, and
    one that doesn't send it gets a well-known stable value rather than a
    silently-broken request."""
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS
    }
    headers.pop("authorization", None)
    headers["content-type"] = "application/json"
    headers["x-api-key"] = incoming_x_api_key if incoming_x_api_key else api_key
    headers.setdefault("anthropic-version", "2023-06-01")
    return headers


def _map_anthropic_usage(usage: dict) -> dict:
    """Translate an Anthropic `usage` object into the OpenAI-shaped dict
    `StatsStore.record_usage` already knows how to read, rather than
    teaching that function a second field-name vocabulary.

    Field semantics (verified against Anthropic's prompt-caching docs,
    2026-09-03 — not guessed): `input_tokens` is *only* the tokens after the
    last cache breakpoint, not the total. The docs give the identity
    ``total_input_tokens = cache_read_input_tokens +
    cache_creation_input_tokens + input_tokens``, so `prompt_tokens` here is
    that sum, `prompt_cache_hit_tokens` is `cache_read_input_tokens`
    (actually served from cache — the direct evidence this project's
    caching claim rests on), and `prompt_cache_miss_tokens` is everything
    else that had to be processed fresh (`input_tokens +
    cache_creation_input_tokens`).
    """
    input_tokens = usage.get("input_tokens") or 0
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0
    return {
        "prompt_tokens": input_tokens + cache_creation + cache_read,
        "completion_tokens": usage.get("output_tokens"),
        "prompt_cache_hit_tokens": cache_read,
        "prompt_cache_miss_tokens": input_tokens + cache_creation,
    }


def _try_parse_anthropic_sse_event(line: str) -> dict | None:
    """Return the parsed JSON object from one Anthropic SSE `data: ...`
    line, or None. Unlike `_try_parse_sse_usage`, this returns the whole
    event object (not just a `usage` sub-field) — the caller needs the
    event `type` to know whether this event's `usage` is the
    `message_start` baseline or a `message_delta` update, since Anthropic
    splits usage across both rather than reporting it once at stream end.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


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


def _record_usage_dict(stats: StatsStore, usage: object, row_id: int) -> None:
    """Shared tail end of usage recording — hand a parsed `usage` dict (from
    either a plain JSON response or a sniffed SSE chunk) to stats and log
    the result. Any failure is swallowed — telemetry must never turn a
    successful upstream call into a proxy error.

    Deliberately synchronous (not offloaded to a thread pool): StatsStore
    holds one sqlite3.Connection shared across every request, currently
    touched only from this single event-loop thread, which asyncio's
    cooperative scheduling already serializes for free. Moving this call
    into a thread pool would make that connection genuinely
    multi-threaded — sqlite3's check_same_thread=False permits that but
    does not make it safe on its own — to save a sub-millisecond single-row
    UPDATE. Not worth the new lock this would require until profiling ever
    shows this is a measured bottleneck (see benchmark/profile_compress.py
    in the roleplay-slim repo for the standard this project holds itself to
    before adding that kind of complexity).
    """
    try:
        recorded = stats.record_usage(usage, row_id)
    except Exception:  # pragma: no cover - record_usage is already tolerant
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


def _record_upstream_usage(stats: StatsStore, resp: httpx.Response, row_id: int) -> None:
    """Pull the provider-reported `usage` block out of a completed
    non-streaming upstream response and hand it to stats.

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
    failure is swallowed for the same reason as _record_usage_dict.
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
    _record_usage_dict(stats, payload.get("usage"), row_id)


# A real usage-bearing SSE line is a couple hundred bytes at most. If a
# single line (no '\n' seen yet) grows past this, something upstream is
# not speaking the expected SSE line-per-event framing — stop trying to
# parse this stream's usage rather than buffering it without bound. This
# never affects what gets forwarded to the client; only usage-sniffing.
_SSE_LINE_BUFFER_CAP = 1 << 20  # 1 MiB


def _try_parse_sse_usage(line: str) -> dict | None:
    """Return the `usage` dict from one SSE `data: ...` line, or None.

    Every OpenAI-compatible provider this project targets (DeepSeek and the
    others openai-adapter.ts in a real client lists) emits one complete
    JSON object per `data:` line — never a value split across multiple
    `data:` lines the way the SSE spec technically allows for multi-line
    fields. Handling that theoretical case would add real complexity for a
    shape none of these providers actually produce, so this deliberately
    doesn't.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        # Not every provider necessarily sends JSON on every data: line
        # (some send SSE comments or keep-alives) — not our problem to
        # flag, this stream's bytes are already on their way to the client
        # unchanged regardless of whether we could make sense of them.
        return None
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


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
    async def get_stats(window: int | None = None):
        result = stats.summary()
        if window is not None:
            if window <= 0:
                return JSONResponse(
                    {"error": {"message": "window must be a positive integer"}}, status_code=400
                )
            # Nested under "recent" rather than replacing the top-level
            # fields — every existing consumer of the all-time numbers
            # (including this project's own tests) keeps working
            # unchanged; window=N is purely additive.
            result["recent"] = stats.summary(window=window)
        return result

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

        _req_model = body.get("model")
        entry = stats.record(messages, compressed, model=_req_model if isinstance(_req_model, str) else None)
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
            _record_upstream_usage(stats, resp, entry["id"])
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
            # Forwarding is byte-for-byte unaffected by anything below —
            # every branch here only ever reads what's already been
            # yielded. aiter_text() (not aiter_bytes()) is deliberate: httpx
            # runs an incremental UTF-8 decoder under the hood, so a
            # multi-byte character split across two network chunks decodes
            # correctly instead of needing to be hand-rolled here. SSE
            # bodies from every provider this proxy targets are UTF-8
            # text/event-stream, so re-encoding what aiter_text() hands
            # back reproduces the original bytes.
            line_buffer = ""
            last_usage: dict | None = None
            try:
                async for text_chunk in resp.aiter_text():
                    yield text_chunk.encode("utf-8")
                    line_buffer += text_chunk
                    if len(line_buffer) > _SSE_LINE_BUFFER_CAP:
                        # Give up parsing this stream's usage rather than
                        # buffering an unbounded line — the client already
                        # has everything forwarded regardless.
                        line_buffer = ""
                        continue
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        usage = _try_parse_sse_usage(line)
                        if usage is not None:
                            # The stream's *last* usage-bearing line is the
                            # authoritative one if a provider ever sends
                            # more than one — keep overwriting rather than
                            # stopping at the first.
                            last_usage = usage
            except httpx.HTTPError as e:
                logger.warning("upstream stream interrupted: %s", e)
            finally:
                # A final usage-bearing line with no trailing newline would
                # otherwise sit unparsed in line_buffer forever.
                if line_buffer:
                    usage = _try_parse_sse_usage(line_buffer)
                    if usage is not None:
                        last_usage = usage
                if last_usage is not None:
                    _record_usage_dict(stats, last_usage, entry["id"])
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

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        """Anthropic-shape counterpart of /v1/chat/completions. See
        anthropic_proxy.py's module docstring and
        docs/designs/anthropic-protocol-support.md for why this is a
        parallel native route rather than a translation layer bolted onto
        the OpenAI one — no code here converts between the two formats.

        404s outright when anthropic_upstream_base_url is unset (the
        default) rather than guessing a path from upstream_base_url, which
        is very often a different path on the same provider (see
        ProxyConfig.anthropic_upstream_base_url's docstring) — silently
        forwarding to a guessed-wrong path would produce a confusing
        upstream 404/401 instead of a clear "this route isn't configured".
        """
        if not config.anthropic_upstream_base_url:
            return JSONResponse(
                {"error": {"message": "anthropic_upstream_base_url is not configured — "
                                       "this proxy's /v1/messages route is disabled"}},
                status_code=404,
            )

        incoming_x_api_key = request.headers.get("x-api-key")
        auth_error, incoming_x_api_key = _check_client_auth_anthropic(
            incoming_x_api_key, config, client_auth_token
        )
        if auth_error is not None:
            return auth_error

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
            compressed = compress_anthropic_messages(
                messages, config.anthropic_keep_recent_turns
            )
        except Exception:
            logger.exception(
                "anthropic compression failed for request #%d", stats.request_count + 1
            )
            return JSONResponse(
                {"error": {"message": "internal error during compression"}},
                status_code=500,
            )

        before_chars = estimate_anthropic_messages_chars(messages)
        after_chars = estimate_anthropic_messages_chars(compressed)
        pct = (before_chars - after_chars) / before_chars * 100 if before_chars else 0.0
        logger.info(
            "anthropic request #%d | %d -> %d chars (saved %.1f%%) | msgs:%d",
            stats.request_count + 1,
            before_chars,
            after_chars,
            pct,
            len(messages),
        )
        # record_raw (not record()) — these are raw char counts, not
        # OpenAI-shaped messages, so nothing here should be re-estimated via
        # a tokenizer that assumes string/text-block content. Recorded
        # through the same tokens_before/after columns the OpenAI route
        # uses; stats.summary()'s percentage math is unit-agnostic (only
        # ever computes before/after ratios), so this is honest as long as
        # the two routes' numbers are never compared as if they were the
        # same unit (chars vs. tokens).
        _req_model = body.get("model")
        entry = stats.record_raw(
            before_chars, after_chars, model=_req_model if isinstance(_req_model, str) else None
        )
        body["messages"] = compressed

        headers = _build_anthropic_upstream_headers(request, incoming_x_api_key, api_key)
        upstream_url = f"{config.anthropic_upstream_base_url.rstrip('/')}/messages"
        qs = request.url.query
        if qs:
            upstream_url = f"{upstream_url}?{qs}"

        is_streaming = bool(body.get("stream"))
        client: httpx.AsyncClient = request.app.state.client

        if not is_streaming:
            try:
                resp = await _send_with_connect_retry(
                    lambda: client.post(upstream_url, json=body, headers=headers)
                )
            except httpx.HTTPError as e:
                logger.warning("anthropic upstream request failed: %s", e)
                return JSONResponse(
                    {"error": {"message": f"upstream request failed: {e}"}}, status_code=502
                )
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", "").lower():
                try:
                    payload = resp.json()
                    if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
                        _record_usage_dict(
                            stats, _map_anthropic_usage(payload["usage"]), entry["id"]
                        )
                except Exception:
                    pass
            return _passthrough_response(resp)

        try:
            resp = await _send_with_connect_retry(
                lambda: client.send(
                    client.build_request("POST", upstream_url, json=body, headers=headers),
                    stream=True,
                )
            )
        except httpx.HTTPError as e:
            logger.warning("anthropic upstream streaming request failed: %s", e)
            return JSONResponse(
                {"error": {"message": f"upstream request failed: {e}"}}, status_code=502
            )

        async def stream_anthropic_upstream() -> AsyncIterator[bytes]:
            # Same byte-for-byte-forwarding guarantee as the OpenAI stream
            # handler: everything below only ever reads what's already been
            # yielded. usage_acc starts from message_start's baseline and is
            # updated (not replaced) by every message_delta's usage, since
            # Anthropic splits usage across both events rather than
            # reporting it once — see _map_anthropic_usage's docstring.
            line_buffer = ""
            usage_acc: dict = {}
            try:
                async for text_chunk in resp.aiter_text():
                    yield text_chunk.encode("utf-8")
                    line_buffer += text_chunk
                    if len(line_buffer) > _SSE_LINE_BUFFER_CAP:
                        line_buffer = ""
                        continue
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        event = _try_parse_anthropic_sse_event(line)
                        if event is None:
                            continue
                        etype = event.get("type")
                        if etype == "message_start":
                            usage = event.get("message", {}).get("usage")
                            if isinstance(usage, dict):
                                usage_acc.update(usage)
                        elif etype == "message_delta":
                            usage = event.get("usage")
                            if isinstance(usage, dict):
                                usage_acc.update(usage)
            except httpx.HTTPError as e:
                logger.warning("anthropic upstream stream interrupted: %s", e)
            finally:
                if line_buffer:
                    event = _try_parse_anthropic_sse_event(line_buffer)
                    if event is not None and event.get("type") == "message_delta":
                        usage = event.get("usage")
                        if isinstance(usage, dict):
                            usage_acc.update(usage)
                if usage_acc:
                    _record_usage_dict(stats, _map_anthropic_usage(usage_acc), entry["id"])
                await resp.aclose()

        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        return StreamingResponse(
            stream_anthropic_upstream(),
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
