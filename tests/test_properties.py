"""Property-based tests for compress().

Rather than hand-picking a handful of fixtures (which is how the real
history_window/dedupe interaction bug on 2026-07-25 slipped through several
rounds of manual testing), these generate a wide variety of message-array
shapes -- varying prefix length, turn count, and which system messages
repeat across turns -- and check invariants that must hold for *any* valid
input, not just the specific ones we thought to write by hand.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from roleplay_slim.compressor import compress
from roleplay_slim.config import CompressorConfig
from roleplay_slim.segmenter import detect_prefix_length

VALID_ROLES = ("system", "user", "assistant")

# A small, deliberately tiny pool of system-message texts so hypothesis
# frequently generates duplicates across turns -- that's the shape that
# actually stresses dedupe_verbatim_tail / history_window / the recurring-
# instruction hoist together.
_SYSTEM_TEXT_POOL = [
    "[FORMAT RULE] end with a tag",
    "[MEMORY] some retrieved context",
    "[TIME] current time is now",
]


@st.composite
def messages_strategy(draw):
    prefix_len = draw(st.integers(min_value=0, max_value=3))
    prefix = [
        {"role": "system", "content": f"persona block {i}"}
        for i in range(prefix_len)
    ]

    n_turns = draw(st.integers(min_value=0, max_value=8))
    dynamic: list[dict] = []
    for i in range(n_turns):
        dynamic.append({"role": "user", "content": f"user turn {i}. Some text here. More text."})
        if draw(st.booleans()):
            dynamic.append({"role": "assistant", "content": f"assistant turn {i}. A reply. Another sentence."})
        n_system = draw(st.integers(min_value=0, max_value=2))
        for _ in range(n_system):
            text = draw(st.sampled_from(_SYSTEM_TEXT_POOL))
            dynamic.append({"role": "system", "content": text})

    # Occasionally end on a bare trailing user message with no reply yet,
    # matching "the request currently in flight" -- the shape that exposed
    # the real bug.
    if draw(st.booleans()):
        dynamic.append({"role": "user", "content": "final pending question"})

    return prefix + dynamic


@st.composite
def config_strategy(draw):
    return CompressorConfig(
        keep_recent_turns=draw(st.integers(min_value=0, max_value=4)),
        enable_whitespace_normalize=draw(st.booleans()),
        enable_dedupe_verbatim_tail=draw(st.booleans()),
        enable_history_window=draw(st.booleans()),
        enable_strip_stage_directions=False,  # exercised separately in test_strategies.py
        history_window_mode=draw(st.sampled_from(["trim", "drop"])),
    )


@given(messages=messages_strategy(), config=config_strategy())
@settings(max_examples=300, deadline=None)
def test_prefix_always_survives_byte_identical(messages, config):
    prefix_len = detect_prefix_length(messages, config.prefix_override)
    original_prefix = messages[:prefix_len]
    result = compress(messages, config)
    assert result[:prefix_len] == original_prefix


@given(messages=messages_strategy(), config=config_strategy())
@settings(max_examples=300, deadline=None)
def test_recurring_system_messages_never_vanish_entirely(messages, config):
    """Regression coverage for the 2026-07-25 bug: any system-message text
    that appears 2+ times in the original input must appear at least once
    in the output, no matter how history_window/dedupe interact."""
    prefix_len = detect_prefix_length(messages, config.prefix_override)
    dynamic_original = messages[prefix_len:]

    counts: dict[str, int] = {}
    for m in dynamic_original:
        if m.get("role") == "system":
            counts[m["content"]] = counts.get(m["content"], 0) + 1
    recurring = {text for text, n in counts.items() if n >= 2}

    result = compress(messages, config)
    surviving_texts = {m.get("content") for m in result if m.get("role") == "system"}

    missing = recurring - surviving_texts
    assert not missing, f"recurring system message(s) vanished entirely: {missing}"


@given(messages=messages_strategy(), config=config_strategy())
@settings(max_examples=300, deadline=None)
def test_output_is_well_formed(messages, config):
    """compress() must never crash on any well-formed input, and must
    never produce a message with an invalid role or non-string content."""
    result = compress(messages, config)
    assert isinstance(result, list)
    for m in result:
        assert m.get("role") in VALID_ROLES
        assert isinstance(m.get("content"), str)


@given(messages=messages_strategy(), config=config_strategy())
@settings(max_examples=300, deadline=None)
def test_never_produces_more_turns_than_input_when_history_window_drops(messages, config):
    """A sanity bound: with drop mode, compression should not somehow
    invent new user/assistant messages that weren't in the input."""
    result = compress(messages, config)
    input_dialogue_count = sum(1 for m in messages if m.get("role") in ("user", "assistant"))
    output_dialogue_count = sum(1 for m in result if m.get("role") in ("user", "assistant"))
    assert output_dialogue_count <= input_dialogue_count
