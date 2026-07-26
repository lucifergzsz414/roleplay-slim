from roleplay_slim.config import STAGE_DIRECTION_PRESETS, CompressorConfig
from roleplay_slim.segmenter import Turn
from roleplay_slim.strategies import (
    _extractive_trim,
    dedupe_verbatim_tail,
    history_window,
    normalize_prefix_timestamps,
    strip_stage_directions,
    whitespace_normalize,
)


def test_whitespace_normalize_collapses_runs_and_trims():
    text = "  hi\n\n\n\nthere   \n"
    assert whitespace_normalize(text) == "hi\n\nthere"


def test_normalize_prefix_timestamps_rounds_down_to_bucket():
    text = "current time: 2026-07-25T14:23:47Z"
    assert normalize_prefix_timestamps(text, 5) == "current time: 2026-07-25T14:20:00Z"


def test_normalize_prefix_timestamps_respects_timezone_offset():
    text = "ts=2026-07-25T09:07:12+08:00"
    assert normalize_prefix_timestamps(text, 10) == "ts=2026-07-25T09:00:00+08:00"


def test_normalize_prefix_timestamps_bucket_zero_is_noop():
    text = "current time: 2026-07-25T14:23:47Z"
    assert normalize_prefix_timestamps(text, 0) == text


def test_normalize_prefix_timestamps_ignores_non_matching_text():
    text = "no timestamps here, just plain roleplay dialogue."
    assert normalize_prefix_timestamps(text, 5) == text


def test_normalize_prefix_timestamps_handles_multiple_occurrences():
    text = "start=2026-07-25T14:23:47Z end=2026-07-25T14:41:02Z"
    assert (
        normalize_prefix_timestamps(text, 15)
        == "start=2026-07-25T14:15:00Z end=2026-07-25T14:30:00Z"
    )


def test_dedupe_verbatim_tail_keeps_last_occurrence_only():
    turns = [
        Turn([
            {"role": "user", "content": "u1"},
            {"role": "system", "content": "FOOTER"},
        ]),
        Turn([
            {"role": "user", "content": "u2"},
            {"role": "system", "content": "FOOTER"},
        ]),
    ]
    result = dedupe_verbatim_tail(turns)
    footer_count = sum(
        1 for t in result for m in t.messages if m.get("content") == "FOOTER"
    )
    assert footer_count == 1
    # the surviving copy must be from the later turn, not the earlier one
    assert result[-1].messages[-1]["content"] == "FOOTER"


def test_dedupe_verbatim_tail_leaves_distinct_system_messages_alone():
    turns = [
        Turn([{"role": "user", "content": "u1"}, {"role": "system", "content": "A"}]),
        Turn([{"role": "user", "content": "u2"}, {"role": "system", "content": "B"}]),
    ]
    result = dedupe_verbatim_tail(turns)
    all_system = [m["content"] for t in result for m in t.messages if m.get("role") == "system"]
    assert set(all_system) == {"A", "B"}


def test_history_window_keeps_recent_turns_verbatim():
    turns = [
        Turn([{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}])
        for i in range(5)
    ]
    config = CompressorConfig(keep_recent_turns=2, history_window_mode="drop")
    result = history_window(turns, config)
    assert len(result) == 2
    assert result[0].messages[0]["content"] == "u3"
    assert result[1].messages[0]["content"] == "u4"


def test_history_window_trim_mode_shortens_but_keeps_older_turns():
    long_text = "第一句话。第二句无关紧要的话。第三句也无关紧要。最后一句话。"
    turns = [
        Turn([{"role": "user", "content": long_text}, {"role": "assistant", "content": "ok"}]),
        Turn([{"role": "user", "content": "recent"}, {"role": "assistant", "content": "recent reply"}]),
    ]
    config = CompressorConfig(keep_recent_turns=1, history_window_mode="trim")
    result = history_window(turns, config)
    assert len(result) == 2
    trimmed_content = result[0].messages[0]["content"]
    assert len(trimmed_content) < len(long_text)
    assert "第一句话" in trimmed_content
    assert "最后一句话" in trimmed_content
    # recent turn untouched
    assert result[1].messages[0]["content"] == "recent"


def test_history_window_noop_when_fewer_turns_than_keep_window():
    turns = [Turn([{"role": "user", "content": "only one"}])]
    config = CompressorConfig(keep_recent_turns=3)
    result = history_window(turns, config)
    assert result == turns


def test_history_window_trim_preserves_tool_messages():
    """Tool-call result messages must survive trim mode — dropping them
    would leave assistant.tool_calls without their corresponding results,
    which many providers reject with a 400."""
    turns = [
        Turn([
            {"role": "user", "content": "what is 2+2"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "calc", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "4"},
            {"role": "assistant", "content": "2+2 equals 4"},
        ]),
        Turn([{"role": "user", "content": "recent"}, {"role": "assistant", "content": "ok"}]),
    ]
    config = CompressorConfig(keep_recent_turns=1, history_window_mode="trim")
    result = history_window(turns, config)
    assert len(result) == 2
    old_roles = [m["role"] for m in result[0].messages]
    assert "tool" in old_roles, f"tool message was dropped, got roles: {old_roles}"


def test_history_window_trim_does_not_trim_tool_content():
    """Tool results are structured data (often JSON) — sentence-boundary
    trimming would corrupt them. They must pass through unmodified."""
    tool_json = '{"result": 42, "confidence": 0.95}'
    turns = [
        Turn([
            {"role": "user", "content": "compute"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": tool_json},
            {"role": "assistant", "content": "done"},
        ]),
        Turn([{"role": "user", "content": "recent"}, {"role": "assistant", "content": "ok"}]),
    ]
    config = CompressorConfig(keep_recent_turns=1, history_window_mode="trim")
    result = history_window(turns, config)
    tool_msgs = [m for m in result[0].messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == tool_json


def test_dedupe_verbatim_tail_handles_developer_messages():
    turns = [
        Turn([
            {"role": "user", "content": "u1"},
            {"role": "developer", "content": "FORMAT REMINDER"},
        ]),
        Turn([
            {"role": "user", "content": "u2"},
            {"role": "developer", "content": "FORMAT REMINDER"},
        ]),
    ]
    result = dedupe_verbatim_tail(turns)
    dev_count = sum(
        1 for t in result for m in t.messages
        if m.get("role") == "developer" and m.get("content") == "FORMAT REMINDER"
    )
    assert dev_count == 1


def test_strip_stage_directions_removes_parens_from_old_turns_only():
    turns = [
        Turn([{"role": "user", "content": "（挥手）第一句台词"}]),
        Turn([{"role": "user", "content": "（微笑）最近这句台词"}]),
    ]
    config = CompressorConfig(stage_direction_pattern=r"（[^）]*）")
    result = strip_stage_directions(turns, config, keep_recent=1)
    assert "（" not in result[0].messages[0]["content"]
    assert "第一句台词" in result[0].messages[0]["content"]
    # the most recent turn (last one) is left fully alone
    assert result[1].messages[0]["content"] == "（微笑）最近这句台词"


def test_extractive_trim_never_returns_longer_than_input():
    """A very short message where the connector "……" plus two short
    sentences already exceeds the original length must be returned
    unchanged — trim must not make things worse."""
    # 6 chars: "a.b.c." → sentences ["a", "b", "c"] → "a …… c" = 7 chars
    short = "a.b.c."
    assert _extractive_trim(short) == short
    # 2 sentences: no trimming needed
    assert _extractive_trim("hello. world.") == "hello. world."


def test_extractive_trim_still_trims_when_beneficial():
    long_text = "第一句很长很长的话。第二句无关紧要的话。第三句也很长很长的话。"
    result = _extractive_trim(long_text)
    assert len(result) < len(long_text)
    assert "第一句" in result
    assert "第三句" in result


def test_asterisk_preset_does_not_match_double_asterisks():
    """The asterisk preset must not match markdown **bold** or
    ***bold-italic*** — otherwise legitimate formatting gets silently
    stripped."""
    pattern = STAGE_DIRECTION_PRESETS["asterisk"]
    import re
    rx = re.compile(pattern)

    # Must match: single-asterisk stage direction
    assert rx.search("*waves*") is not None
    assert rx.search("hello *smiles* there") is not None

    # Must NOT match: double-asterisk bold
    assert rx.search("**important**") is None, "asterisk preset matched **bold**"
    # Must NOT match: triple-asterisk bold-italic
    assert rx.search("***really***") is None, "asterisk preset matched ***bold-italic***"
    # Must NOT match: single asterisk with nothing inside
    assert rx.search("**") is None
