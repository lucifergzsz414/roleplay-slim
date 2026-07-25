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


def _make_client(handler) -> TestClient:
    config = ProxyConfig(compressor=CompressorConfig(keep_recent_turns=1))
    app = create_app(config, transport=httpx.MockTransport(handler))
    return TestClient(app)


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
