"""Tests for the proxy layer. The *upstream* HTTP call is replaced with
httpx.MockTransport so these run with no real network access — they check
the proxy's own behavior (compression call-through, header passthrough,
streaming), not the real provider's.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import roleplay_slim.proxy.server as proxy_server
from roleplay_slim.anthropic_proxy import estimate_anthropic_messages_tokens
from roleplay_slim.config import CompressorConfig, ProxyConfig, StatsConfig
from roleplay_slim.proxy.server import create_app
from roleplay_slim.stats import estimate_messages_tokens

FOOTER = "[FORMAT RULE] end with a tag"


def _sample_body(stream: bool = False) -> dict:
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": FOOTER},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "system", "content": FOOTER},
        {"role": "user", "content": "final pending question"},
    ]
    return {"model": "test-model", "stream": stream, "messages": messages}


def _make_client(handler, config: ProxyConfig | None = None) -> TestClient:
    # Default to the in-memory store so these tests don't litter a stats.db
    # into the working tree; the durability tests below opt into a real file.
    config = config or ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=False),
    )
    app = create_app(config, transport=httpx.MockTransport(handler))
    # __enter__ (not just construction) is what actually runs the app's
    # lifespan, which is where the shared httpx.AsyncClient gets created —
    # see server.py's create_app().
    return TestClient(app).__enter__()


def test_healthz():
    client = _make_client(lambda request: httpx.Response(200, json={}))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stats_starts_at_zero():
    client = _make_client(lambda request: httpx.Response(200, json={}))
    resp = client.get("/stats")
    assert resp.json()["request_count"] == 0


def test_stats_window_param_adds_recent_block_without_changing_top_level():
    """?window=N is purely additive — the existing top-level fields (what
    every current consumer, including the other tests in this file, reads)
    must be byte-identical to the no-window response; window=N only adds
    a nested "recent" block."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _make_client(handler)
    client.post("/v1/chat/completions", json=_sample_body())
    client.post("/v1/chat/completions", json=_sample_body())

    plain = client.get("/stats").json()
    windowed = client.get("/stats", params={"window": 1}).json()

    assert "recent" not in plain
    without_recent = {k: v for k, v in windowed.items() if k != "recent"}
    assert without_recent == plain
    assert windowed["recent"]["request_count"] == 1


def test_stats_window_must_be_positive():
    client = _make_client(lambda request: httpx.Response(200, json={}))
    resp = client.get("/stats", params={"window": 0})
    assert resp.status_code == 400
    resp = client.get("/stats", params={"window": -3})
    assert resp.status_code == 400


def test_chat_completions_sends_compressed_messages_upstream():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 200
    sent_messages = captured["body"]["messages"]
    # prefix (first message) must reach upstream untouched
    assert sent_messages[0] == {"role": "system", "content": "persona"}
    # the repeated footer must survive exactly once (not vanish, not duplicate)
    footer_count = sum(1 for m in sent_messages if m.get("content") == FOOTER)
    assert footer_count == 1
    # fewer messages reached upstream than were sent in (something got compressed)
    assert len(sent_messages) < len(_sample_body()["messages"])
    # A blank override remains fully backwards-compatible.
    assert captured["body"]["model"] == "test-model"


def test_chat_completions_uses_configured_upstream_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = ProxyConfig(
        upstream_model="qwen3.6-plus",
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=False),
    )
    client = _make_client(handler, config)

    response = client.post("/v1/chat/completions", json=_sample_body())

    assert response.status_code == 200
    assert captured["body"]["model"] == "qwen3.6-plus"


def test_chat_completions_updates_stats():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _make_client(handler)
    client.post("/v1/chat/completions", json=_sample_body())

    stats = client.get("/stats").json()
    assert stats["request_count"] == 1
    assert stats["tokens_before_total"] > 0


def test_authorization_header_passthrough():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Authorization": "Bearer caller-supplied-key"},
    )
    assert captured["auth"] == "Bearer caller-supplied-key"


def test_empty_bearer_token_falls_back_to_configured_upstream_key(monkeypatch):
    """Some client apps always send "Authorization: Bearer " (no token
    after it) when their own API key setting is empty — that's not a real
    credential and must not be forwarded as-is, since the real upstream
    would just 401 it. The proxy's own configured key should be used
    instead, exactly as if no Authorization header had been sent at all."""
    monkeypatch.setenv("ROLEPLAY_SLIM_TEST_EMPTY_BEARER_KEY", "sk-real-upstream-key")
    config = ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        upstream_api_key_env="ROLEPLAY_SLIM_TEST_EMPTY_BEARER_KEY",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler, config=config)
    client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Authorization": "Bearer "},
    )
    assert captured["auth"] == "Bearer sk-real-upstream-key"


def test_warns_at_creation_when_no_upstream_key_is_configured(caplog):
    """A forgotten UPSTREAM_API_KEY otherwise fails silently until the first
    request hits a confusing 401 from upstream — this should be visible
    immediately when the app is created instead."""
    config = ProxyConfig(
        compressor=CompressorConfig(),
        upstream_api_key_env="ROLEPLAY_SLIM_TEST_DEFINITELY_UNSET_KEY",
    )
    with caplog.at_level("WARNING", logger="roleplay_slim"):
        create_app(config, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert any("ROLEPLAY_SLIM_TEST_DEFINITELY_UNSET_KEY" in r.message for r in caplog.records)


def test_no_warning_when_upstream_key_is_configured(caplog, monkeypatch):
    monkeypatch.setenv("ROLEPLAY_SLIM_TEST_KEY_IS_SET", "sk-something")
    config = ProxyConfig(
        compressor=CompressorConfig(),
        upstream_api_key_env="ROLEPLAY_SLIM_TEST_KEY_IS_SET",
    )
    with caplog.at_level("WARNING", logger="roleplay_slim"):
        create_app(config, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert not any("not set" in r.message for r in caplog.records)


def test_chat_completions_logs_a_readable_compression_summary(caplog):
    """The proxy is typically run in a foreground terminal — a readable
    per-request log line (not just the /stats JSON endpoint) is how most
    people actually notice compression is working."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _make_client(handler)
    with caplog.at_level("INFO", logger="roleplay_slim"):
        client.post("/v1/chat/completions", json=_sample_body())

    messages = [r.message for r in caplog.records if r.name == "roleplay_slim"]
    assert any("request #1" in m and "tokens" in m for m in messages)


def test_streaming_passthrough_is_not_corrupted():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        chunks = [b"data: {\"delta\": \"a\"}\n\n", b"data: [DONE]\n\n"]
        return httpx.Response(200, content=b"".join(chunks), headers={"content-type": "text/event-stream"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        body = b"".join(resp.iter_bytes())
    assert b"[DONE]" in body
    assert b"delta" in body


def test_streaming_usage_is_recorded_in_stats():
    """The core gap this closes: streaming responses used to leave
    /stats' upstream block permanently null, because the proxy never
    parsed anything out of the SSE bytes it was passing through — even
    though the final chunk (when the client asks for stream_options.
    include_usage, which every real client this project targets does)
    carries the same `usage` block a non-streaming response's body has."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n',
            b'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4, '
            b'"prompt_cache_hit_tokens": 6, "prompt_cache_miss_tokens": 4}}\n\n',
            b"data: [DONE]\n\n",
        ]
        return httpx.Response(200, content=b"".join(chunks), headers={"content-type": "text/event-stream"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        b"".join(resp.iter_bytes())  # drain

    stats = client.get("/stats").json()
    assert stats["upstream"]["usage_sample_count"] == 1
    assert stats["upstream"]["prompt_tokens_total"] == 10
    assert stats["upstream"]["completion_tokens_total"] == 4
    assert stats["upstream"]["cache_hit_tokens_total"] == 6


def test_streaming_usage_records_the_last_chunk_not_the_first():
    """If a provider ever emits more than one usage-bearing line in a
    single stream, the last one is authoritative (it reflects the final
    token count once generation actually finished) — an earlier one could
    in principle be a provisional/incomplete figure."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            b'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 1}}\n\n',
            b'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 9}}\n\n',
            b"data: [DONE]\n\n",
        ]
        return httpx.Response(200, content=b"".join(chunks), headers={"content-type": "text/event-stream"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        b"".join(resp.iter_bytes())

    stats = client.get("/stats").json()
    assert stats["upstream"]["completion_tokens_total"] == 9


def test_streaming_usage_survives_a_line_split_across_network_chunks():
    """SSE bytes don't arrive pre-aligned to line boundaries — a real
    network chunk boundary can land in the middle of a `data: {...}` JSON
    object (and, separately, in the middle of a multi-byte UTF-8
    character). Both must still be parsed correctly since the proxy
    buffers text across chunks rather than parsing each chunk in
    isolation."""
    async def stream_gen():
        # Split both a plain-ASCII JSON line AND a multi-byte UTF-8
        # character (in "内" = E5 86 85) across chunk boundaries.
        yield 'data: {"choices": [{"delta": {"content": "五'.encode()
        yield "内".encode()[:1]  # first byte of a 3-byte UTF-8 char
        yield "内".encode()[1:] + b'"}}]}\n\n'
        yield b'data: {"choices": [], "usage": {"prompt_tokens": 7, '
        yield b'"completion_tokens": 2}}\n\n'
        yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream_gen(), headers={"content-type": "text/event-stream"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        body = b"".join(resp.iter_bytes())

    # Forwarding must still be byte-perfect despite the mid-character split.
    assert "五内".encode() in body

    stats = client.get("/stats").json()
    assert stats["upstream"]["completion_tokens_total"] == 2


def test_streaming_usage_absent_leaves_upstream_stats_null():
    """A provider that never sends stream_options.include_usage (or a
    client that never asked for it) must not make /stats lie about having
    a measurement — see upstream_summary()'s "None means no measurement,
    not measured zero" contract."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n', b"data: [DONE]\n\n"]
        return httpx.Response(200, content=b"".join(chunks), headers={"content-type": "text/event-stream"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        b"".join(resp.iter_bytes())

    stats = client.get("/stats").json()
    assert stats["upstream"] is None


def test_streaming_propagates_real_upstream_error_status():
    """A non-2xx upstream response for a streaming request must reach the
    caller with the real status code, not a hardcoded 200 — the caller
    can't otherwise tell a rate-limit or auth error from a real reply."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b'{"error": "rate limited"}', headers={"content-type": "application/json"})

    client = _make_client(handler)
    with client.stream("POST", "/v1/chat/completions", json=_sample_body(stream=True)) as resp:
        body = b"".join(resp.iter_bytes())
    assert resp.status_code == 429
    assert b"rate limited" in body


def test_non_streaming_upstream_connection_failure_returns_502_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.status_code == 502
    assert "error" in resp.json()


def test_streaming_upstream_connection_failure_returns_502_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body(stream=True))
    assert resp.status_code == 502
    assert "error" in resp.json()


def test_client_auth_rejects_request_with_no_token_configured():
    """client_auth_token_env set but the underlying env var empty should
    fail closed (reject everyone) rather than fail open (accept anyone) —
    an operator who set this expects protection, not a silent no-op."""
    config = ProxyConfig(
        compressor=CompressorConfig(),
        client_auth_token_env="ROLEPLAY_SLIM_TEST_AUTH_TOKEN_UNSET",
    )
    client = _make_client(lambda r: httpx.Response(200, json={}), config=config)
    resp = client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


def test_client_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("ROLEPLAY_SLIM_TEST_AUTH_TOKEN", "correct-secret")
    config = ProxyConfig(
        compressor=CompressorConfig(),
        client_auth_token_env="ROLEPLAY_SLIM_TEST_AUTH_TOKEN",
    )
    client = _make_client(lambda r: httpx.Response(200, json={}), config=config)
    resp = client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


def test_client_auth_accepts_correct_token_and_does_not_forward_it_upstream(monkeypatch):
    monkeypatch.setenv("ROLEPLAY_SLIM_TEST_AUTH_TOKEN_2", "correct-secret")
    monkeypatch.setenv("ROLEPLAY_SLIM_TEST_UPSTREAM_KEY", "sk-real-upstream-key")
    config = ProxyConfig(
        compressor=CompressorConfig(),
        client_auth_token_env="ROLEPLAY_SLIM_TEST_AUTH_TOKEN_2",
        upstream_api_key_env="ROLEPLAY_SLIM_TEST_UPSTREAM_KEY",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler, config=config)
    resp = client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Authorization": "Bearer correct-secret"},
    )
    assert resp.status_code == 200
    # the proxy-access token must not leak upstream — the real upstream key
    # (from ROLEPLAY_SLIM_TEST_UPSTREAM_KEY) is what should be sent instead
    assert captured["auth"] == "Bearer sk-real-upstream-key"


def test_no_client_auth_configured_is_backward_compatible():
    """Default (client_auth_token_env="") behavior is completely unaffected
    — no Authorization header required at all, matching every test above
    this one in the file that never sets it."""
    client = _make_client(lambda r: httpx.Response(200, json={"choices": []}))
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.status_code == 200


# ── multi-token client auth (client_auth_tokens_extra) ───────────────────


def _auth_client(monkeypatch, extra: str, env_token: str = ""):
    monkeypatch.setenv("PROXY_AUTH_TEST_TOKEN", env_token)
    config = ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        client_auth_token_env="PROXY_AUTH_TEST_TOKEN" if env_token else "",
        client_auth_tokens_extra=extra,
    )
    return _make_client(lambda r: httpx.Response(200, json={"choices": []}), config=config)


def test_client_auth_accepts_each_extra_token(monkeypatch):
    client = _auth_client(monkeypatch, "sk-aaa, Operit_bbb ,sk-ccc")
    for token in ("sk-aaa", "Operit_bbb", "sk-ccc"):
        resp = client.post(
            "/v1/chat/completions", json=_sample_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, token


def test_client_auth_rejects_token_not_in_extra(monkeypatch):
    client = _auth_client(monkeypatch, "sk-aaa,Operit_bbb")
    resp = client.post(
        "/v1/chat/completions", json=_sample_body(),
        headers={"Authorization": "Bearer sk-zzz"},
    )
    assert resp.status_code == 401


def test_client_auth_extra_alone_still_gates(monkeypatch):
    """extras configured but client_auth_token_env empty — the gate is still
    active (fail closed), not a silent no-op."""
    client = _auth_client(monkeypatch, "sk-aaa")
    assert client.post(
        "/v1/chat/completions", json=_sample_body(),
        headers={"Authorization": "Bearer sk-aaa"},
    ).status_code == 200
    assert client.post(
        "/v1/chat/completions", json=_sample_body(),
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_client_auth_combines_env_and_extra(monkeypatch):
    """The configured env token and the extras are both accepted, and the
    proxy-access token still must not leak upstream."""
    monkeypatch.setenv("PROXY_AUTH_TEST_TOKEN", "Operit_main")
    monkeypatch.setenv("PROXY_UPSTREAM_KEY_TEST", "sk-real-upstream-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    config = ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        client_auth_token_env="PROXY_AUTH_TEST_TOKEN",
        client_auth_tokens_extra="sk-bot-extra",
        upstream_api_key_env="PROXY_UPSTREAM_KEY_TEST",
    )
    client = _make_client(handler, config=config)

    for token in ("Operit_main", "sk-bot-extra"):
        resp = client.post(
            "/v1/chat/completions", json=_sample_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, token
    # neither the env token nor the extra leaks upstream — the real upstream
    # key is forwarded instead
    assert captured["auth"] == "Bearer sk-real-upstream-key"


# ── P0-3: request body validation ────────────────────────────────────────


def test_request_body_not_json_returns_400():
    client = _make_client(lambda r: httpx.Response(200, json={}))
    resp = client.post(
        "/v1/chat/completions",
        content=b"not valid json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "JSON" in resp.json()["error"]["message"]


def test_request_body_not_dict_returns_400():
    client = _make_client(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/chat/completions", json=["not", "a", "dict"])
    assert resp.status_code == 400
    assert "object" in resp.json()["error"]["message"]


def test_messages_not_array_returns_400():
    client = _make_client(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/chat/completions", json={"model": "x", "messages": "not-an-array"})
    assert resp.status_code == 400
    assert "array" in resp.json()["error"]["message"]


def test_messages_elements_not_dicts_returns_400():
    client = _make_client(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/chat/completions", json={"model": "x", "messages": ["not-a-dict"]})
    assert resp.status_code == 400
    assert "object" in resp.json()["error"]["message"]


def test_empty_messages_array_is_valid():
    """An empty messages array is unusual but not malformed — it should
    pass validation (let the upstream decide whether to reject it)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json={"model": "x", "messages": []})
    assert resp.status_code == 200


# ── P0-2: non-JSON upstream response pass-through ────────────────────────


def test_non_json_upstream_response_is_passed_through():
    """A CDN, gateway, or reverse proxy may return HTML/text error pages —
    the proxy must forward them as-is instead of crashing on resp.json()."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            content=b"<html><body>bad gateway</body></html>",
            headers={"content-type": "text/html"},
        )

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.status_code == 502
    assert b"bad gateway" in resp.content


def test_empty_upstream_response_is_passed_through():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, content=b"")

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.status_code == 204
    assert resp.content == b""


# ── P0-4: header and query-param forwarding ──────────────────────────────


def test_upstream_response_headers_are_forwarded():
    """Useful upstream response headers like Retry-After and X-Request-ID
    must reach the caller, not be silently swallowed by the proxy."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": "rate limited"},
            headers={"retry-after": "9", "x-request-id": "abc-123"},
        )

    client = _make_client(handler)
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.headers.get("retry-after") == "9"
    assert resp.headers.get("x-request-id") == "abc-123"


def test_request_headers_are_forwarded_to_upstream():
    """Custom request headers (OpenAI-Organization, X-Custom, etc.) must
    reach the upstream provider, not be silently dropped."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["openai-organization"] = request.headers.get("openai-organization")
        captured["x-custom"] = request.headers.get("x-custom")
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"OpenAI-Organization": "org-123", "X-Custom": "trace-me"},
    )
    assert captured["openai-organization"] == "org-123"
    assert captured["x-custom"] == "trace-me"


def test_query_params_are_forwarded_to_upstream():
    """Query-string parameters appended to the proxy URL must be forwarded
    to the upstream so that provider-specific URL params work transparently."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    client.post("/v1/chat/completions?customer_id=test-456", json=_sample_body())
    assert "customer_id=test-456" in captured["url"]


def test_compression_crash_returns_500_with_openai_error_format(monkeypatch):
    """If compress() itself throws an unexpected exception (not a validation
    error — the input is well-formed — but a bug/data-dependent edge case),
    the proxy must return a 500 with an OpenAI-shaped error body rather than
    crashing with a bare unhandled-exception stack trace."""
    import roleplay_slim.proxy.server as srv

    def _blow_up(*_a, **_kw):
        raise RuntimeError("simulated internal compressor bug")

    monkeypatch.setattr(srv, "compress", _blow_up)

    client = _make_client(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/chat/completions", json=_sample_body())
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body
    assert "message" in body["error"]
    assert "compression" in body["error"]["message"]


def test_hop_by_hop_request_headers_are_stripped():
    """Hop-by-hop headers (keep-alive, proxy-authorization, te, trailers,
    upgrade, etc.) belong to the proxy's own connection to its peer — they
    must never be forwarded to the upstream.

    Note: httpx's AsyncClient auto-adds a Connection header to outbound
    requests even when we don't include one in our headers dict — that's
    correct HTTP/1.1 behavior, not a proxy bug. The test uses hop-by-hop
    headers that httpx does NOT auto-add (keep-alive, te) for the check.
    """
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["keep-alive"] = request.headers.get("keep-alive")
        captured["te"] = request.headers.get("te")
        captured["upgrade"] = request.headers.get("upgrade")
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    client.post(
        "/v1/chat/completions",
        json=_sample_body(),
        headers={"Keep-Alive": "timeout=5", "TE": "trailers", "Upgrade": "websocket"},
    )
    assert captured["keep-alive"] is None
    assert captured["te"] is None
    assert captured["upgrade"] is None


# --- upstream usage capture ---------------------------------------------
#
# Everything else /stats reports is an estimate produced by running an
# OpenAI tokenizer over text bound for some other provider. These tests
# cover the one part that is a real measurement: the provider's own
# accounting, lifted out of the response body on the way through.


def _usage_response(usage: dict | None) -> httpx.Response:
    payload: dict = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


def test_upstream_usage_is_recorded_from_response():
    client = _make_client(
        lambda request: _usage_response(
            {
                "prompt_tokens": 1234,
                "completion_tokens": 56,
                "prompt_cache_hit_tokens": 512,
                "prompt_cache_miss_tokens": 722,
            }
        )
    )
    client.post("/v1/chat/completions", json=_sample_body())

    upstream = client.get("/stats").json()["upstream"]
    assert upstream["usage_sample_count"] == 1
    assert upstream["prompt_tokens_total"] == 1234
    assert upstream["completion_tokens_total"] == 56
    assert upstream["cache_hit_tokens_total"] == 512
    assert upstream["cache_miss_tokens_total"] == 722
    # 512 / (512 + 722)
    assert upstream["cache_hit_pct"] == pytest.approx(41.49, abs=0.01)


def test_upstream_usage_totals_accumulate_across_requests():
    client = _make_client(
        lambda request: _usage_response({"prompt_tokens": 100, "completion_tokens": 10})
    )
    for _ in range(3):
        client.post("/v1/chat/completions", json=_sample_body())

    upstream = client.get("/stats").json()["upstream"]
    assert upstream["usage_sample_count"] == 3
    assert upstream["prompt_tokens_total"] == 300
    assert upstream["completion_tokens_total"] == 30


def test_upstream_block_is_null_before_any_usage_seen():
    """No measurement and a measured zero are different claims — the block
    stays null rather than reporting zeroes that look like real data."""
    client = _make_client(lambda request: httpx.Response(200, json={}))
    assert client.get("/stats").json()["upstream"] is None


def test_provider_without_cache_accounting_reports_null_cache_fields():
    """OpenAI-compatible providers that don't expose prefix-cache figures
    still contribute prompt/completion totals — the cache fields stay null
    instead of being reported as a 0% hit rate."""
    client = _make_client(
        lambda request: _usage_response({"prompt_tokens": 80, "completion_tokens": 20})
    )
    client.post("/v1/chat/completions", json=_sample_body())

    upstream = client.get("/stats").json()["upstream"]
    assert upstream["prompt_tokens_total"] == 80
    assert upstream["cache_hit_tokens_total"] is None
    assert upstream["cache_hit_pct"] is None


def test_response_without_usage_is_passed_through_unaffected():
    client = _make_client(lambda request: _usage_response(None))
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ok"
    assert client.get("/stats").json()["upstream"] is None


def test_non_json_error_page_does_not_break_usage_capture():
    """A CDN/gateway error page is the exact case the raw-passthrough
    design exists for — usage capture must not reintroduce a parse that
    turns it into a proxy-side 500."""
    client = _make_client(
        lambda request: httpx.Response(
            502, html="<html><body>Bad Gateway</body></html>"
        )
    )
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 502
    assert "Bad Gateway" in resp.text
    assert client.get("/stats").json()["upstream"] is None


def test_malformed_json_body_claiming_json_content_type_is_tolerated():
    client = _make_client(
        lambda request: httpx.Response(
            200,
            content=b"{not valid json",
            headers={"content-type": "application/json"},
        )
    )
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 200
    assert resp.content == b"{not valid json"
    assert client.get("/stats").json()["upstream"] is None


def test_usage_with_unexpected_field_types_is_ignored_not_fatal():
    """Some OpenAI-compatible gateways return floats or nulls here. A
    wrong number is worse than a missing one when these figures are the
    evidence behind the cache claim, so unparseable fields are dropped."""
    client = _make_client(
        lambda request: _usage_response(
            {
                "prompt_tokens": 40.0,
                "completion_tokens": None,
                "prompt_cache_hit_tokens": "lots",
            }
        )
    )
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 200
    upstream = client.get("/stats").json()["upstream"]
    assert upstream["prompt_tokens_total"] == 40
    assert upstream["completion_tokens_total"] == 0
    assert upstream["cache_hit_tokens_total"] is None


def test_estimated_stats_still_reported_alongside_upstream():
    """The estimate isn't replaced by the real figures — both are useful:
    the estimate covers the compression delta (before/after, which the
    provider never sees), the upstream block covers what was actually
    billed."""
    client = _make_client(
        lambda request: _usage_response({"prompt_tokens": 999, "completion_tokens": 1})
    )
    client.post("/v1/chat/completions", json=_sample_body())

    summary = client.get("/stats").json()
    assert summary["request_count"] == 1
    assert summary["tokens_before_total"] > 0
    assert summary["tokens_after_total"] > 0
    assert summary["upstream"]["prompt_tokens_total"] == 999


# --- non-chat endpoint passthrough --------------------------------------
#
# Real clients call more than /v1/chat/completions. SillyTavern, OpenWebUI
# and friends fetch GET /v1/models on connect to populate a model picker,
# and a 404 there reads as a broken server before the user sends anything.


def test_models_endpoint_is_forwarded_upstream():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={"object": "list", "data": [{"id": "deepseek-chat"}]})

    client = _make_client(handler)
    resp = client.get("/v1/models")

    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "deepseek-chat"
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/models")


def test_nested_passthrough_path_is_preserved():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "deepseek-chat"})

    client = _make_client(handler)
    assert client.get("/v1/models/deepseek-chat").status_code == 200
    assert captured["url"].endswith("/models/deepseek-chat")


def test_passthrough_forwards_query_string():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={})

    client = _make_client(handler)
    client.get("/v1/models?customer_id=abc")
    assert "customer_id=abc" in captured["url"]


def test_passthrough_post_forwards_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": []})

    client = _make_client(handler)
    resp = client.post("/v1/embeddings", json={"model": "e5", "input": "hello"})

    assert resp.status_code == 200
    assert captured["body"] == {"model": "e5", "input": "hello"}


def test_passthrough_does_not_compress():
    """These endpoints carry no `messages` array, and the request body must
    arrive upstream byte-identical to what the caller sent."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={})

    payload = {"model": "e5", "input": ["a", "b", "c"]}
    client = _make_client(handler)
    client.post("/v1/embeddings", json=payload)

    assert json.loads(captured["raw"]) == payload


def test_catch_all_does_not_shadow_chat_completions():
    """Registration-order regression guard: if the catch-all ever matched
    /v1/chat/completions first, compression would silently stop happening
    and this project would become a plain forwarding proxy."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)  # keep_recent_turns=1
    resp = client.post("/v1/chat/completions", json=_sample_body())

    assert resp.status_code == 200
    # Compression actually ran: fewer messages went upstream than came in.
    assert len(captured["body"]["messages"]) < len(_sample_body()["messages"])


def test_upstream_error_status_passes_through():
    client = _make_client(
        lambda request: httpx.Response(404, json={"error": {"message": "no such model"}})
    )
    resp = client.get("/v1/models/nope")

    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "no such model"


def test_passthrough_upstream_connection_failure_returns_502():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)
    resp = client.get("/v1/models")

    assert resp.status_code == 502
    assert "upstream request failed" in resp.json()["error"]["message"]


def test_passthrough_requires_proxy_credentials_when_configured(monkeypatch):
    """The security point of the catch-all: a route that spends the
    upstream API key without checking proxy auth would let anyone who can
    reach this port bill calls to the operator's provider account."""
    monkeypatch.setenv("PROXY_SECRET", "s3cret")
    config = ProxyConfig(
        client_auth_token_env="PROXY_SECRET",
        compressor=CompressorConfig(keep_recent_turns=1),
    )
    client = _make_client(lambda request: httpx.Response(200, json={}), config=config)

    assert client.get("/v1/models").status_code == 401
    assert client.get(
        "/v1/models", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.get(
        "/v1/models", headers={"Authorization": "Bearer s3cret"}
    ).status_code == 200


def test_passthrough_does_not_forward_proxy_credential_upstream(monkeypatch):
    """The proxy's own secret authenticates access to the proxy — it is not
    an upstream credential and must never reach the provider."""
    monkeypatch.setenv("PROXY_SECRET", "s3cret")
    monkeypatch.setenv("UPSTREAM_API_KEY", "real-upstream-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    config = ProxyConfig(
        client_auth_token_env="PROXY_SECRET",
        compressor=CompressorConfig(keep_recent_turns=1),
    )
    client = _make_client(handler, config=config)
    client.get("/v1/models", headers={"Authorization": "Bearer s3cret"})

    assert captured["auth"] == "Bearer real-upstream-key"


def test_local_endpoints_are_not_swallowed_by_catch_all():
    client = _make_client(lambda request: httpx.Response(200, json={}))

    assert client.get("/healthz").json() == {"status": "ok"}
    assert "request_count" in client.get("/stats").json()


# --- stats persistence --------------------------------------------------
#
# With persist=true, /stats answers from a SQLite file, so the numbers
# survive a proxy restart. These tests bring up two successive apps on the
# same database file and assert the totals carry over.


def _make_persistent_client(handler, db_path, config=None):
    config = config or ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=True, db_path=db_path),
    )
    app = create_app(config, transport=httpx.MockTransport(handler))
    return TestClient(app).__enter__()


def test_stats_survive_a_proxy_restart(tmp_path):
    db = str(tmp_path / "stats.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client1 = _make_persistent_client(handler, db)
    client1.post("/v1/chat/completions", json=_sample_body())
    client1.post("/v1/chat/completions", json=_sample_body())
    assert client1.get("/stats").json()["request_count"] == 2
    client1.__exit__(None, None, None)  # runs lifespan teardown → store closed

    client2 = _make_persistent_client(handler, db)
    stats = client2.get("/stats").json()
    client2.__exit__(None, None, None)
    assert stats["request_count"] == 2
    assert stats["tokens_before_total"] > 0


def test_upstream_usage_survives_a_proxy_restart(tmp_path):
    db = str(tmp_path / "stats2.db")
    config = ProxyConfig(
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=True, db_path=db),
    )

    def usage_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                },
            },
        )

    client1 = _make_persistent_client(usage_handler, db, config)
    client1.post("/v1/chat/completions", json=_sample_body())
    client1.__exit__(None, None, None)

    client2 = _make_persistent_client(usage_handler, db, config)
    upstream = client2.get("/stats").json()["upstream"]
    client2.__exit__(None, None, None)
    assert upstream["prompt_tokens_total"] == 100
    assert upstream["cache_hit_tokens_total"] == 40
    assert upstream["cache_hit_pct"] == 40.0


# ---------------------------------------------------------------------------
# /v1/messages — Anthropic-shape route (parallel to /v1/chat/completions,
# not a translation of it — see docs/designs/anthropic-protocol-support.md)
# ---------------------------------------------------------------------------

def _anthropic_body(stream: bool = False) -> dict:
    return {
        "model": "claude-test",
        "stream": stream,
        "system": "you are a helpful assistant",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "q1"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a.py"}},
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "OLD OUTPUT " * 20}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            {"role": "user", "content": [{"type": "text", "text": "final pending question"}]},
        ],
    }


def test_anthropic_route_404s_when_not_configured():
    """Default config leaves anthropic_upstream_base_url empty — the route
    must refuse cleanly, not guess a path from upstream_base_url (which is
    very often a different path on the same provider)."""
    client = _make_client(lambda request: httpx.Response(200, json={}))
    resp = client.post("/v1/messages", json=_anthropic_body())
    assert resp.status_code == 404


def _make_anthropic_client(handler, **overrides) -> TestClient:
    config = ProxyConfig(
        anthropic_upstream_base_url="https://example.test/anthropic/v1",
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=False),
        **overrides,
    )
    app = create_app(config, transport=httpx.MockTransport(handler))
    return TestClient(app).__enter__()


def test_anthropic_route_forwards_to_correct_upstream_path_and_headers():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x-api-key"] = request.headers.get("x-api-key")
        captured["anthropic-version"] = request.headers.get("anthropic-version")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    import os

    os.environ["TEST_UPSTREAM_KEY"] = "sk-configured"
    try:
        # create_app() reads upstream_api_key_env at creation time, so the
        # env var must be set *before* the client (and its app) is built.
        client = _make_anthropic_client(handler, upstream_api_key_env="TEST_UPSTREAM_KEY")
        resp = client.post("/v1/messages", json=_anthropic_body())
    finally:
        del os.environ["TEST_UPSTREAM_KEY"]

    assert resp.status_code == 200
    assert captured["url"] == "https://example.test/anthropic/v1/messages"
    assert captured["x-api-key"] == "sk-configured"
    assert captured["anthropic-version"] == "2023-06-01"
    assert captured["authorization"] is None  # never forwards Authorization on this route


def test_anthropic_route_retries_transient_connect_failure(monkeypatch):
    monkeypatch.setattr(proxy_server, "_CONNECT_RETRY_DELAYS", (0.0, 0.0))
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary connection reset", request=request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = _make_anthropic_client(handler)
    resp = client.post("/v1/messages", json=_anthropic_body())

    assert resp.status_code == 200
    assert attempts == 2


def test_anthropic_stream_retries_transient_connect_failure(monkeypatch):
    monkeypatch.setattr(proxy_server, "_CONNECT_RETRY_DELAYS", (0.0, 0.0))
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("temporary TLS timeout", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"type":"message_stop"}\n\n',
        )

    client = _make_anthropic_client(handler)
    resp = client.post("/v1/messages", json=_anthropic_body(stream=True))

    assert resp.status_code == 200
    assert resp.content == b'data: {"type":"message_stop"}\n\n'
    assert attempts == 2


def test_anthropic_route_stops_after_bounded_connect_retries(monkeypatch):
    monkeypatch.setattr(proxy_server, "_CONNECT_RETRY_DELAYS", (0.0, 0.0))
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("still unavailable", request=request)

    client = _make_anthropic_client(handler)
    resp = client.post("/v1/messages", json=_anthropic_body())

    assert resp.status_code == 502
    assert attempts == 3


def test_anthropic_route_does_not_retry_after_connection_is_established(monkeypatch):
    monkeypatch.setattr(proxy_server, "_CONNECT_RETRY_DELAYS", (0.0, 0.0))
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("response reset after request was sent", request=request)

    client = _make_anthropic_client(handler)
    resp = client.post("/v1/messages", json=_anthropic_body())

    assert resp.status_code == 502
    assert attempts == 1


def test_anthropic_route_client_supplied_api_key_wins():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["x-api-key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={})

    client = _make_anthropic_client(handler)
    client.post(
        "/v1/messages",
        json=_anthropic_body(),
        headers={"x-api-key": "sk-client-own-key", "anthropic-version": "2024-01-01"},
    )
    assert captured["x-api-key"] == "sk-client-own-key"


def test_anthropic_route_preserves_client_supplied_version_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["anthropic-version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json={})

    client = _make_anthropic_client(handler)
    client.post("/v1/messages", json=_anthropic_body(), headers={"anthropic-version": "2024-01-01"})
    assert captured["anthropic-version"] == "2024-01-01"


def test_anthropic_route_system_field_forwarded_untouched():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _make_anthropic_client(handler)
    body = _anthropic_body()
    client.post("/v1/messages", json=body)
    assert captured["body"]["system"] == body["system"]


def test_anthropic_route_trims_old_tool_result_before_forwarding():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    # keep_recent_turns=1 via anthropic_keep_recent_turns default (6) would
    # keep this short 2-turn conversation entirely — force it down to 1 to
    # actually exercise trimming in this small fixture.
    client = _make_anthropic_client(handler, anthropic_keep_recent_turns=1)
    client.post("/v1/messages", json=_anthropic_body())

    forwarded_messages = captured["body"]["messages"]
    tool_result_block = forwarded_messages[2]["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert tool_result_block["content"] == "[older tool output omitted by roleplay-slim]"
    # The final (most recent) user turn is untouched
    assert forwarded_messages[-1]["content"][0]["text"] == "final pending question"


def test_anthropic_route_proxy_auth_rejects_missing_or_wrong_key():
    import os

    os.environ["TEST_ANTHROPIC_CLIENT_TOKEN"] = "secret-proxy-token"
    try:
        # Same ordering requirement as the api-key test above — the env var
        # must exist before create_app() runs.
        client = _make_anthropic_client(
            lambda r: httpx.Response(200, json={}),
            client_auth_token_env="TEST_ANTHROPIC_CLIENT_TOKEN",
        )
        no_key = client.post("/v1/messages", json=_anthropic_body())
        wrong_key = client.post("/v1/messages", json=_anthropic_body(), headers={"x-api-key": "wrong"})
        right_key = client.post(
            "/v1/messages", json=_anthropic_body(), headers={"x-api-key": "secret-proxy-token"}
        )
    finally:
        del os.environ["TEST_ANTHROPIC_CLIENT_TOKEN"]

    assert no_key.status_code == 401
    assert wrong_key.status_code == 401
    assert right_key.status_code == 200


def test_anthropic_route_records_usage_with_correct_field_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 50,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 1800,
                    "output_tokens": 503,
                },
            },
        )

    client = _make_anthropic_client(handler)
    client.post("/v1/messages", json=_anthropic_body())
    upstream = client.get("/stats").json()["upstream"]

    # total_input = input_tokens + cache_creation + cache_read (Anthropic's
    # own documented identity, see _map_anthropic_usage's docstring)
    assert upstream["prompt_tokens_total"] == 50 + 20 + 1800
    assert upstream["completion_tokens_total"] == 503
    assert upstream["cache_hit_tokens_total"] == 1800


def test_anthropic_route_streaming_merges_usage_from_message_start_and_delta():
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            b'event: message_start\n'
            b'data: {"type": "message_start", "message": {"usage": {"input_tokens": 472, '
            b'"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 2}}}\n\n',
            b'event: content_block_delta\n'
            b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}\n\n',
            b'event: message_delta\n'
            b'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, '
            b'"usage": {"output_tokens": 89}}\n\n',
            b'event: message_stop\ndata: {"type": "message_stop"}\n\n',
        ]
        return httpx.Response(200, content=b"".join(chunks), headers={"content-type": "text/event-stream"})

    client = _make_anthropic_client(handler)
    with client.stream("POST", "/v1/messages", json=_anthropic_body(stream=True)) as resp:
        b"".join(resp.iter_bytes())

    upstream = client.get("/stats").json()["upstream"]
    # input side only ever appears in message_start; output side is
    # overwritten by message_delta's cumulative final value (89, not 2)
    assert upstream["prompt_tokens_total"] == 472
    assert upstream["completion_tokens_total"] == 89


def test_anthropic_route_streaming_forwards_bytes_unchanged():
    raw_chunks = [
        b'event: message_start\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 5}}}\n\n',
        b'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"text": "hello"}}\n\n',
        b'event: message_stop\ndata: {"type": "message_stop"}\n\n',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"".join(raw_chunks), headers={"content-type": "text/event-stream"})

    client = _make_anthropic_client(handler)
    with client.stream("POST", "/v1/messages", json=_anthropic_body(stream=True)) as resp:
        received = b"".join(resp.iter_bytes())
    assert received == b"".join(raw_chunks)


def test_anthropic_stream_ignores_malformed_usage_without_breaking_forwarding():
    raw = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":"bad"}}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":true}}\n\n'
    )

    client = _make_anthropic_client(
        lambda _request: httpx.Response(
            200, content=raw, headers={"content-type": "text/event-stream"}
        )
    )
    with client.stream("POST", "/v1/messages", json=_anthropic_body(stream=True)) as resp:
        received = b"".join(resp.iter_bytes())

    assert received == raw


def test_openai_and_anthropic_requests_share_one_token_unit_in_stats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"content": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = ProxyConfig(
        anthropic_upstream_base_url="https://example.test/anthropic/v1",
        compressor=CompressorConfig(keep_recent_turns=1),
        stats=StatsConfig(persist=False),
    )
    client = _make_client(handler, config)
    openai_body = _sample_body()
    anthropic_body = _anthropic_body()

    client.post("/v1/chat/completions", json=openai_body)
    client.post("/v1/messages", json=anthropic_body)
    summary = client.get("/stats").json()

    assert summary["request_count"] == 2
    assert summary["tokens_before_total"] == (
        estimate_messages_tokens(openai_body["messages"])
        + estimate_anthropic_messages_tokens(anthropic_body["messages"])
    )
