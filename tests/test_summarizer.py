"""Tests for the summarizer hook (history_window_mode="summarize").

This is the library's one escape hatch from being purely rule-based: it
takes no ML dependency itself, it just hands the aged-out history to the
caller's own condensing function. The tests below are mostly about what
happens when that function misbehaves — it is user code, usually a network
call to an LLM, and a proxy serving a live request must survive it failing.
"""
from __future__ import annotations

import logging

import pytest

from roleplay_slim.compressor import compress
from roleplay_slim.config import CompressorConfig
from roleplay_slim.stats import estimate_messages_tokens

PREFIX = {"role": "system", "content": "You are a character."}


def _conversation(n_turns: int) -> list[dict]:
    messages = [dict(PREFIX)]
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})
    return messages


def _config(summarizer, **kwargs) -> CompressorConfig:
    kwargs.setdefault("keep_recent_turns", 2)
    return CompressorConfig(
        history_window_mode="summarize", summarizer=summarizer, **kwargs
    )


def test_old_turns_collapse_into_one_summary_message():
    out = compress(_conversation(10), _config(lambda msgs: "They talked about things."))

    assert out[0] == PREFIX
    assert out[1] == {"role": "system", "content": "They talked about things."}
    # Prefix + summary + the 2 recent turns (2 messages each).
    assert len(out) == 1 + 1 + 4


def test_summarizer_receives_only_the_aged_out_messages():
    captured = {}

    def summarizer(msgs):
        captured["msgs"] = list(msgs)
        return "summary"

    compress(_conversation(10), _config(summarizer))

    contents = [m["content"] for m in captured["msgs"]]
    # 10 turns, 2 kept recent -> turns 0..7 aged out.
    assert "question 0" in contents
    assert "answer 7" in contents
    # The recent window must not be handed to the summarizer.
    assert "question 8" not in contents
    assert "question 9" not in contents
    # Nor may the cache-stable prefix.
    assert PREFIX["content"] not in contents


def test_recent_turns_are_left_verbatim():
    out = compress(_conversation(10), _config(lambda msgs: "summary"))

    tail = [m for m in out if m["role"] in ("user", "assistant")]
    assert [m["content"] for m in tail] == [
        "question 8", "answer 8", "question 9", "answer 9",
    ]


def test_summarizer_not_called_when_nothing_has_aged_out():
    """Don't spend a network call summarizing an empty block."""
    calls = []
    compress(_conversation(2), _config(lambda msgs: calls.append(msgs) or "x"))
    assert calls == []


def test_empty_summary_drops_the_block():
    """An empty return means "this history isn't worth keeping"."""
    out = compress(_conversation(10), _config(lambda msgs: ""))

    assert out[0] == PREFIX
    assert not any(m["content"] == "" for m in out)
    assert [m["content"] for m in out if m["role"] == "user"] == ["question 8", "question 9"]


def test_whitespace_only_summary_is_treated_as_empty():
    out = compress(_conversation(10), _config(lambda msgs: "   \n  "))
    assert [m["content"] for m in out if m["role"] == "user"] == ["question 8", "question 9"]


def test_summarizer_exception_falls_back_to_trim_without_raising(caplog):
    """The critical one: the callback is usually an LLM call that can time
    out, and a live request must not fail because of it."""
    def exploding(msgs):
        raise RuntimeError("upstream summarizer timed out")

    with caplog.at_level(logging.ERROR, logger="roleplay_slim"):
        out = compress(_conversation(10), _config(exploding))

    assert out[0] == PREFIX
    # History survived via the trim path rather than vanishing.
    assert len([m for m in out if m["role"] == "user"]) > 2
    assert any("summarizer raised" in r.getMessage() for r in caplog.records)


def test_summarizer_returning_non_string_falls_back_to_trim(caplog):
    with caplog.at_level(logging.WARNING, logger="roleplay_slim"):
        out = compress(_conversation(10), _config(lambda msgs: {"not": "a string"}))

    assert len([m for m in out if m["role"] == "user"]) > 2
    assert any("expected str" in r.getMessage() for r in caplog.records)


def test_summarizer_returning_none_falls_back_to_trim():
    out = compress(_conversation(10), _config(lambda msgs: None))
    assert len([m for m in out if m["role"] == "user"]) > 2


def test_summarize_actually_shrinks_hard_to_compress_text():
    """The case extractive trim cannot touch: short chat lines with no
    sentence-ending punctuation. This is what the hook exists for."""
    messages = [dict(PREFIX)]
    for i in range(40):
        messages.append({"role": "user", "content": f"在吗 {i} " * 10})
        messages.append({"role": "assistant", "content": f"嗯 我在 {i} " * 12})

    trimmed = compress(messages, CompressorConfig(keep_recent_turns=2))
    summarized = compress(messages, _config(lambda msgs: "早前聊了些日常。"))

    assert estimate_messages_tokens(summarized) < estimate_messages_tokens(trimmed) / 5


def test_summarize_composes_with_token_budget():
    """Both features at once: the summary is produced first, then the
    budget still gets the final say on total size."""
    messages = _conversation(40)
    out = compress(
        messages,
        _config(lambda msgs: "a very long summary. " * 200, max_prompt_tokens=100),
    )
    assert estimate_messages_tokens(out) <= 100


def test_prefix_untouched_in_summarize_mode():
    out = compress(_conversation(10), _config(lambda msgs: "summary"))
    assert out[0] == PREFIX


# --- configuration validation -------------------------------------------


def test_summarize_mode_without_summarizer_is_rejected():
    with pytest.raises(ValueError, match="requires a summarizer callable"):
        CompressorConfig(history_window_mode="summarize")


def test_unknown_history_window_mode_still_rejected():
    with pytest.raises(ValueError, match="history_window_mode"):
        CompressorConfig(history_window_mode="condense")


def test_summarizer_cannot_be_set_from_toml(tmp_path):
    """A TOML value would sail through the field-name filter and then fail
    at call time deep inside compression — reject it up front instead."""
    path = tmp_path / "c.toml"
    path.write_text('[compressor]\nsummarizer = "my_module.summarize"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="summarizer cannot be set from a config file"):
        CompressorConfig.from_toml(path)


def test_summarize_mode_from_toml_then_assigned_in_python(tmp_path):
    """The documented path: load the file, then attach the callable."""
    path = tmp_path / "c.toml"
    path.write_text(
        '[compressor]\nhistory_window_mode = "trim"\nkeep_recent_turns = 2\n',
        encoding="utf-8",
    )
    config = CompressorConfig.from_toml(path)
    config.summarizer = lambda msgs: "summary"
    config.history_window_mode = "summarize"

    out = compress(_conversation(10), config)
    assert out[1] == {"role": "system", "content": "summary"}


def test_existing_modes_unaffected():
    """Regression guard: adding a third mode must not disturb the two that
    deployments already rely on."""
    messages = _conversation(10)
    trim = compress(messages, CompressorConfig(keep_recent_turns=2, history_window_mode="trim"))
    drop = compress(messages, CompressorConfig(keep_recent_turns=2, history_window_mode="drop"))

    assert [m["content"] for m in drop if m["role"] == "user"] == ["question 8", "question 9"]
    assert len(trim) > len(drop)
