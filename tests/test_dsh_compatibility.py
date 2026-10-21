"""Opt-in real DSH adapter -> loopback proxy -> fixed upstream fixture.

Set DSH_NODE_MODULES to a node_modules directory containing installed DSH
packages. This never loads user profiles, credentials, sessions, or tools.
It proves transport compatibility, not model quality or provider billing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from roleplay_slim.config import CompressorConfig, ProxyConfig, StatsConfig
from roleplay_slim.proxy.server import create_app


@pytest.mark.parametrize("mode", ["preserve", "trim"])
def test_installed_dsh_adapter_round_trip(monkeypatch, mode):
    module_root = os.environ.get("DSH_NODE_MODULES")
    if not module_root:
        pytest.skip("Set DSH_NODE_MODULES to opt into the installed DSH adapter probe")
    monkeypatch.setenv("ROLEPLAY_SLIM_DSH_FIXTURE_KEY", "local-fixture-not-a-secret")
    captured = []

    def upstream(request):
        body = json.loads(request.content)
        captured.append(body)
        assert request.url.host == "fixture.invalid"
        assert request.url.path == "/v1/chat/completions"
        tool = len(captured) == 2
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_fixture",
                        "type": "function",
                        "function": {
                            "name": "fixture_lookup",
                            "arguments": '{"query":"test"}',
                        },
                    }
                ]
            }
            if tool
            else {"content": "测试回复"}
        )
        chunks = [
            {
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ]
            },
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if tool else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        ]
        payload = (
            "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, text=payload, headers={"content-type": "text/event-stream"}
        )

    config = ProxyConfig(
        upstream_base_url="https://fixture.invalid/v1",
        upstream_api_key_env="ROLEPLAY_SLIM_DSH_FIXTURE_KEY",
        compressor=CompressorConfig(keep_recent_turns=2, history_window_mode=mode),
        stats=StatsConfig(persist=False),
    )
    app = create_app(config, transport=httpx.MockTransport(upstream))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while (
                not server.started and thread.is_alive() and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert server.started, "Test proxy did not start"
            completed = subprocess.run(
                [
                    "node",
                    str(Path(__file__).parent / "fixtures" / "dsh_protocol_probe.mjs"),
                    module_root,
                    f"http://127.0.0.1:{port}/v1",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
            assert '"toolExecuted":false' in completed.stdout
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Test proxy did not stop"
    assert len(captured) == 3
    for body in captured:
        assert body["model"] == "fixture-model"
        assert body["messages"][0] == {
            "role": "system",
            "content": "Persona: preserve exactly.\nNo real actions.",
        }
        assert body["stream"] is True
        assert body["max_tokens"] == 1024
        middle_survives = any(
            "Middle detail" in str(m.get("content")) for m in body["messages"][1:3]
        )
        assert middle_survives is (mode == "preserve")
    assert captured[0]["messages"][-1]["content"] == "Current question: keep me intact."
    assert captured[1]["tools"][0]["function"]["name"] == "fixture_lookup"
    replay = captured[2]["messages"]
    assert replay[-2]["tool_calls"][0]["id"] == "call_fixture"
    function = replay[-2]["tool_calls"][0]["function"]
    assert function["name"] == "fixture_lookup"
    assert json.loads(function["arguments"]) == {"query": "test"}
    assert replay[-1]["tool_call_id"] == "call_fixture"
    assert replay[-1]["content"] == "fixture-result"
