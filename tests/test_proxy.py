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

from roleplay_slim.config import CompressorConfig, ProxyConfig
from roleplay_slim.proxy.server import create_app

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
    config = config or ProxyConfig(compressor=CompressorConfig(keep_recent_turns=1))
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
