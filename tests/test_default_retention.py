"""Default compression must not guess which sentences contain key facts."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from roleplay_slim import CompressorConfig, compress
from roleplay_slim.config import ProxyConfig, StatsConfig
from roleplay_slim.proxy.server import create_app


def _aged_conversation(text, role="user"):
    messages = [{"role": "system", "content": "Persona: unchanged."}]
    messages.extend(
        [
            {"role": "user", "content": text if role == "user" else "Old question."},
            {"role": "assistant", "content": text if role == "assistant" else "Noted."},
            {"role": "system", "content": "Unique memory: this trip was cancelled."},
        ]
    )
    for i in range(8):
        messages.extend(
            [
                {"role": "user", "content": f"Ordinary question {i}."},
                {"role": "assistant", "content": "Ordinary answer."},
            ]
        )
    return messages


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize(
    "text",
    [
        "Opening sentence. The secret code is MID926. Closing sentence.",
        "今天聊了安排。不要买花生，我对花生过敏。周末再说。",
        "We made plans. Correction: Friday, not Thursday. Talk later.",
        "先聊点别的。微信的暗号是青柠，QQ的暗号是海盐。下次继续。",
    ],
)
def test_default_preserves_middle_facts_and_unique_memory(text, role):
    messages = _aged_conversation(text, role)
    original = copy.deepcopy(messages)
    assert compress(messages) == original
    assert messages == original


@pytest.mark.parametrize("failure", ["exception", "none", "invalid"])
def test_failed_summary_preserves_original_history(failure):
    def summarize(_messages):
        if failure == "exception":
            raise RuntimeError("fixture timeout")
        return None if failure == "none" else {}

    messages = _aged_conversation("Opening. Critical fact: MID926. Closing.")
    config = CompressorConfig(history_window_mode="summarize", summarizer=summarize)
    assert compress(messages, config) == messages


@pytest.mark.parametrize("failure", ["exception", "empty"])
def test_mutating_failed_summary_cannot_change_original_nested_content(failure):
    messages = _aged_conversation("Opening. Critical fact: MID926. Closing.")
    messages[1]["content"] = [{"type": "text", "text": "Nested fact: MID926"}]
    original = copy.deepcopy(messages)

    def summarize(old):
        old[0]["content"][0]["text"] = "lost"
        if failure == "exception":
            raise RuntimeError("fixture failure after mutation")
        return ""

    out = compress(
        messages,
        CompressorConfig(history_window_mode="summarize", summarizer=summarize),
    )
    assert messages == original
    assert out == original


def test_anthropic_proxy_preserves_old_tool_facts_by_default():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Find the date."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "Opening. Appointment is Friday. Closing.",
                }
            ],
        },
    ]
    messages.extend(
        {"role": "user", "content": [{"type": "text", "text": f"Later {i}"}]}
        for i in range(8)
    )
    captured = []

    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={})

    app = create_app(
        ProxyConfig(
            anthropic_upstream_base_url="https://fixture.invalid",
            stats=StatsConfig(persist=False),
        ),
        transport=httpx.MockTransport(upstream),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            json={"model": "fixture", "max_tokens": 32, "messages": messages},
        )
    assert response.status_code == 200
    assert captured[0]["messages"] == messages


def test_shipped_config_defaults_preserve_history():
    root = Path(__file__).resolve().parents[1]
    example = ProxyConfig.from_toml(root / "examples" / "example_config.toml")
    assert example.compressor.history_window_mode == "preserve"
    module = ast.parse(
        (root / "integrations" / "launcher" / "launcher_gui.py").read_text(
            encoding="utf-8-sig"
        )
    )
    template = next(
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CONFIG_TOML" for t in node.targets)
    )
    assert 'history_window_mode = "preserve"' in template
    assert not example.compressor.enable_strip_stage_directions
    assert example.compressor.max_prompt_tokens is None
    assert not example.anthropic_trim_tool_results


@pytest.mark.parametrize("value", ["true", 1, None])
def test_anthropic_lossy_opt_in_requires_real_boolean(value):
    with pytest.raises(TypeError, match="anthropic_trim_tool_results"):
        ProxyConfig(anthropic_trim_tool_results=value)


def test_anthropic_lossy_opt_in_can_be_read_from_toml(tmp_path):
    path = tmp_path / "fixture.toml"
    path.write_text(
        "[proxy]\nanthropic_trim_tool_results = true\n[compressor]\n", encoding="utf-8"
    )
    assert ProxyConfig.from_toml(path).anthropic_trim_tool_results is True


def test_explicit_legacy_lossy_settings_produce_startup_warning(caplog):
    config = ProxyConfig(
        compressor=CompressorConfig(history_window_mode="trim"),
        stats=StatsConfig(persist=False),
    )
    with caplog.at_level("WARNING", logger="roleplay_slim"):
        app = create_app(
            config,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        with TestClient(app):
            pass
    assert any(
        "Lossy compression is explicitly enabled" in record.message
        for record in caplog.records
    )
