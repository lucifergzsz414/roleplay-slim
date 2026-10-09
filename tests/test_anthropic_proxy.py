"""Unit tests for anthropic_proxy.py — the Anthropic-messages-shape
compression logic, kept separate from strategies.py/segmenter.py's tests
since it targets a different content shape (see anthropic_proxy.py's
module docstring)."""
from __future__ import annotations

from roleplay_slim.anthropic_proxy import (
    compress_anthropic_messages,
    estimate_anthropic_messages_chars,
    estimate_anthropic_messages_tokens,
    trim_old_tool_results,
)


def _turn(user_text: str, tool_id: str, tool_result_text: str, assistant_text: str) -> list[dict]:
    """One user->tool_use->tool_result->assistant round trip, i.e. one
    "turn" the way segmenter.split_into_turns groups it (starts at user,
    ends right before the next user message)."""
    return [
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me check"},
                {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"path": "x.py"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": tool_result_text}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
    ]


def test_estimate_chars_counts_text_tool_use_and_tool_result_blocks():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "abc"}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}]},
    ]
    # "hello"(5) + "hi"(2) + json.dumps({"path":"abc"})(15) + "file contents"(13)
    import json

    expected = len("hello") + len("hi") + len(json.dumps({"path": "abc"})) + len("file contents")
    assert estimate_anthropic_messages_chars(messages) == expected


def test_estimate_chars_ignores_non_list_content_gracefully():
    # A plain string content (not the Anthropic shape, but shouldn't crash)
    messages = [{"role": "user", "content": "plain string"}]
    assert estimate_anthropic_messages_chars(messages) == len("plain string")


def test_estimate_tokens_uses_the_shared_token_unit() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello world"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "input": {"path": "example.py"}},
            ],
        },
    ]

    assert estimate_anthropic_messages_tokens(messages) > 0
    assert estimate_anthropic_messages_tokens([]) == 0


def test_trim_replaces_tool_result_only_in_older_turns():
    messages = []
    messages += _turn("q1", "tool_1", "BIG OLD OUTPUT " * 50, "a1")
    messages += _turn("q2", "tool_2", "BIG RECENT OUTPUT " * 50, "a2")

    trimmed = trim_old_tool_results(messages, keep_recent_turns=1)

    # Old turn's tool_result content is replaced
    old_tool_result = trimmed[2]["content"][0]
    assert old_tool_result["type"] == "tool_result"
    assert old_tool_result["content"] == "[older tool output omitted by roleplay-slim]"
    assert old_tool_result["tool_use_id"] == "tool_1"  # metadata preserved

    # Recent turn's tool_result content is untouched
    recent_tool_result = trimmed[6]["content"][0]
    assert recent_tool_result["content"] == "BIG RECENT OUTPUT " * 50


def test_trim_preserves_text_and_tool_use_blocks_in_older_turns():
    messages = _turn("q1", "tool_1", "old output", "a1") + _turn("q2", "tool_2", "recent output", "a2")
    trimmed = trim_old_tool_results(messages, keep_recent_turns=1)

    # Older turn's user question and assistant text/tool_use are unchanged
    assert trimmed[0]["content"][0]["text"] == "q1"
    assert trimmed[1]["content"][0]["text"] == "let me check"
    assert trimmed[1]["content"][1]["type"] == "tool_use"
    assert trimmed[1]["content"][1]["input"] == {"path": "x.py"}
    assert trimmed[3]["content"][0]["text"] == "a1"


def test_trim_keep_recent_turns_zero_trims_everything():
    messages = _turn("q1", "tool_1", "output", "a1")
    trimmed = trim_old_tool_results(messages, keep_recent_turns=0)
    assert trimmed[2]["content"][0]["content"] == "[older tool output omitted by roleplay-slim]"


def test_trim_keep_recent_turns_larger_than_history_changes_nothing():
    messages = _turn("q1", "tool_1", "output", "a1")
    trimmed = trim_old_tool_results(messages, keep_recent_turns=99)
    assert trimmed == messages


def test_multiple_tool_results_in_one_human_turn_stay_together() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "inspect both files"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "first"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "second"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]

    assert trim_old_tool_results(messages, keep_recent_turns=1) == messages


def test_trim_messages_with_no_tool_result_blocks_are_untouched():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        {"role": "user", "content": [{"type": "text", "text": "how are you"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "good"}]},
    ]
    trimmed = trim_old_tool_results(messages, keep_recent_turns=1)
    assert trimmed == messages


def test_compress_anthropic_messages_measurably_reduces_size_for_multi_turn_tool_use():
    messages = []
    for i in range(5):
        messages += _turn(f"q{i}", f"tool_{i}", "BIG TOOL OUTPUT LINE\n" * 30, f"a{i}")

    before = estimate_anthropic_messages_chars(messages)
    compressed = compress_anthropic_messages(messages, keep_recent_turns=1)
    after = estimate_anthropic_messages_chars(compressed)

    assert after < before
    assert (before - after) / before > 0.5  # meaningful, not token-level noise


def test_compress_anthropic_messages_never_mutates_the_input_list():
    messages = _turn("q1", "tool_1", "output", "a1") + _turn("q2", "tool_2", "output2", "a2")
    import copy

    original = copy.deepcopy(messages)
    compress_anthropic_messages(messages, keep_recent_turns=1)
    assert messages == original
