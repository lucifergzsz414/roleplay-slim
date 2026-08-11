"""Tests for max_prompt_tokens budget enforcement.

Every other setting in CompressorConfig is structural ("keep the last N
turns") and says nothing about how large the result is. These cover the
one setting that puts an actual ceiling on the output, plus the invariants
the budget is not allowed to break to get under that ceiling.
"""
from __future__ import annotations

import logging

import pytest

from roleplay_slim.compressor import compress
from roleplay_slim.config import CompressorConfig
from roleplay_slim.stats import estimate_messages_tokens

PREFIX = {"role": "system", "content": "You are a character. " * 20}


def _conversation(n_turns: int, filler: str = "sentence one. sentence two. sentence three. ") -> list[dict]:
    """A prefix plus n_turns of user/assistant dialogue. The filler carries
    real sentence punctuation so the extractive trim in history_window has
    something to work with — otherwise it returns text unchanged and these
    tests would be measuring the wrong thing."""
    messages = [dict(PREFIX)]
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"question {i}. {filler}"})
        messages.append({"role": "assistant", "content": f"answer {i}. {filler}"})
    return messages


def test_budget_off_by_default_is_byte_identical():
    """Regression guard for every existing deployment: a config that never
    mentions max_prompt_tokens must behave exactly as it did before the
    setting existed."""
    messages = _conversation(30)
    without = compress(messages, CompressorConfig(keep_recent_turns=4))
    explicit_off = compress(
        messages, CompressorConfig(keep_recent_turns=4, max_prompt_tokens=None)
    )
    assert without == explicit_off


def test_budget_brings_prompt_under_ceiling():
    messages = _conversation(60)
    uncapped = compress(messages, CompressorConfig(keep_recent_turns=2))
    budget = estimate_messages_tokens(uncapped) // 3

    capped = compress(
        messages, CompressorConfig(keep_recent_turns=2, max_prompt_tokens=budget)
    )
    assert estimate_messages_tokens(capped) <= budget
    assert len(capped) < len(uncapped)


def test_budget_that_is_already_met_changes_nothing():
    """A generous budget must not provoke pointless pruning."""
    messages = _conversation(10)
    config_off = CompressorConfig(keep_recent_turns=3)
    baseline = compress(messages, config_off)

    generous = estimate_messages_tokens(baseline) * 10
    capped = compress(
        messages, CompressorConfig(keep_recent_turns=3, max_prompt_tokens=generous)
    )
    assert capped == baseline


def test_prefix_is_never_dropped_even_when_it_alone_exceeds_budget():
    """The cache-stable prefix is the entire premise of the library — an
    unmeetable budget must not be met by sacrificing it."""
    messages = _conversation(20)
    prefix_tokens = estimate_messages_tokens([PREFIX])

    out = compress(
        messages,
        CompressorConfig(keep_recent_turns=1, max_prompt_tokens=max(1, prefix_tokens // 4)),
    )
    assert out[0] == PREFIX


def test_final_turn_survives_an_impossible_budget():
    """Dropping the pending question to save tokens deletes the request."""
    messages = _conversation(20)
    out = compress(
        messages, CompressorConfig(keep_recent_turns=1, max_prompt_tokens=1)
    )

    assert out[0] == PREFIX
    # The last user message the caller sent is still the last user message
    # being asked about. Compared stripped because whitespace_normalize
    # legitimately trims trailing space from every dynamic message.
    last_user_in = [m for m in messages if m["role"] == "user"][-1]
    last_user_out = [m for m in out if m["role"] == "user"][-1]
    assert last_user_out["content"] == last_user_in["content"].strip()


def test_budget_min_recent_turns_is_respected():
    messages = _conversation(40)
    out = compress(
        messages,
        CompressorConfig(
            keep_recent_turns=1, max_prompt_tokens=1, budget_min_recent_turns=5
        ),
    )
    # 5 turns survive, each contributing at least its user message.
    assert len([m for m in out if m["role"] == "user"]) == 5


def test_unmeetable_budget_logs_a_warning(caplog):
    messages = _conversation(20)
    with caplog.at_level(logging.WARNING, logger="roleplay_slim"):
        compress(messages, CompressorConfig(keep_recent_turns=1, max_prompt_tokens=1))

    assert any("max_prompt_tokens" in r.getMessage() for r in caplog.records)


def test_unmeetable_budget_does_not_raise():
    """Best-effort, never fatal: a proxy in the middle of a live request
    must not 500 because the operator set an unrealistic ceiling."""
    out = compress(_conversation(5), CompressorConfig(max_prompt_tokens=1))
    assert out  # something usable came back


def test_tool_call_chain_is_not_orphaned_by_budget_pruning():
    """Turns break at each new user message, so an assistant tool_calls
    block and the tool results answering it stay in the same turn. Dropping
    whole turns therefore can't strand a tool_calls with no results — which
    providers reject outright."""
    messages = [dict(PREFIX)]
    for i in range(12):
        messages.append({"role": "user", "content": f"look up {i}. more text here. and more."})
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": "search", "arguments": "{}"}}
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": f"result {i}"})
        messages.append({"role": "assistant", "content": f"found it {i}. that is all."})

    out = compress(
        messages, CompressorConfig(keep_recent_turns=1, max_prompt_tokens=120)
    )

    call_ids = {
        tc["id"]
        for m in out
        if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    }
    result_ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    assert call_ids == result_ids


def test_recurring_system_message_survives_budget_pruning():
    """The 2026-07-25 failure mode, re-checked against the new pass: a
    mandatory format instruction repeated every turn must not vanish just
    because budget pruning removed every turn that carried a copy."""
    footer = {"role": "system", "content": "[FORMAT RULE] always end with a tag"}
    messages = [dict(PREFIX)]
    for i in range(15):
        messages.append({"role": "user", "content": f"q{i}. filler sentence. another one."})
        messages.append({"role": "assistant", "content": f"a{i}. filler sentence. another one."})
        messages.append(dict(footer))

    out = compress(
        messages, CompressorConfig(keep_recent_turns=1, max_prompt_tokens=60)
    )
    assert any(m.get("content") == footer["content"] for m in out)


def test_budget_counts_the_prefix_toward_the_ceiling():
    """A budget is a statement about the whole prompt, not just the part
    this library is allowed to prune."""
    small_prefix = [{"role": "system", "content": "short."}]
    big_prefix = [{"role": "system", "content": "very long persona. " * 60}]
    tail = [
        {"role": "user", "content": "q1. one. two."},
        {"role": "assistant", "content": "a1. one. two."},
        {"role": "user", "content": "q2. one. two."},
        {"role": "assistant", "content": "a2. one. two."},
        {"role": "user", "content": "q3. one. two."},
    ]
    config = CompressorConfig(keep_recent_turns=1, max_prompt_tokens=80)

    with_small = compress(small_prefix + tail, config)
    with_big = compress(big_prefix + tail, config)

    # The larger prefix eats the budget, forcing more history to go.
    assert len(with_big) < len(with_small)


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_invalid_max_prompt_tokens_rejected(bad):
    with pytest.raises(ValueError, match="max_prompt_tokens"):
        CompressorConfig(max_prompt_tokens=bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_budget_min_recent_turns_rejected(bad):
    with pytest.raises(ValueError, match="budget_min_recent_turns"):
        CompressorConfig(budget_min_recent_turns=bad)


def test_budget_is_configurable_from_toml(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        "[compressor]\nmax_prompt_tokens = 4096\nbudget_min_recent_turns = 3\n",
        encoding="utf-8",
    )
    config = CompressorConfig.from_toml(path)
    assert config.max_prompt_tokens == 4096
    assert config.budget_min_recent_turns == 3
