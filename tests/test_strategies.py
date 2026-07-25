from roleplay_slim.config import CompressorConfig
from roleplay_slim.segmenter import Turn
from roleplay_slim.strategies import (
    dedupe_verbatim_tail,
    history_window,
    strip_stage_directions,
    whitespace_normalize,
)


def test_whitespace_normalize_collapses_runs_and_trims():
    text = "  hi\n\n\n\nthere   \n"
    assert whitespace_normalize(text) == "hi\n\nthere"


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
