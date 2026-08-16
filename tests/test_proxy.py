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

from roleplay_slim.config import CompressorConfig, ProxyConfig, StatsConfig
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
