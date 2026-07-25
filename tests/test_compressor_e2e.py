"""End-to-end test using a fictional multi-turn roleplay conversation shaped
like a real production bot's message array (persona + shared-block prefix,
history, footer reminder repeated every turn) — no real user data."""
from roleplay_slim.compressor import compress
from roleplay_slim.config import CompressorConfig

SYSTEM_PROMPT = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED_BLOCK = "[Session context — not part of the roleplay]\nFormat rules apply."
FOOTER = "[FORMAT RULE] End your reply with a mood tag. Usually neutral."


def _turn(user_text: str, assistant_text: str) -> list[dict]:
    return [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
        {"role": "system", "content": FOOTER},
    ]


def build_fixture_messages() -> list[dict]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": SHARED_BLOCK},
    ]
    for i in range(6):
        messages.extend(
            _turn(
                f"（走进房间）第{i}次台词，说了一堆无关紧要的寒暄内容用来撑长度。",
                f"（抬头看了一眼）回复第{i}次，同样也是一堆不痛不痒的场面话用来撑长度。",
            )
        )
    return messages


def test_prefix_is_byte_identical_after_compression():
    messages = build_fixture_messages()
    config = CompressorConfig(keep_recent_turns=2, enable_strip_stage_directions=True)
    result = compress(messages, config)
    assert result[0] == messages[0]
    assert result[1] == messages[1]


def test_dynamic_region_is_meaningfully_shorter():
    messages = build_fixture_messages()
    config = CompressorConfig(keep_recent_turns=2, enable_strip_stage_directions=True)
    result = compress(messages, config)

    before_len = sum(len(m.get("content", "")) for m in messages[2:])
    after_len = sum(len(m.get("content", "")) for m in result[2:])
    assert after_len < before_len


def test_recent_turns_preserve_stage_directions_and_full_text():
    messages = build_fixture_messages()
    config = CompressorConfig(keep_recent_turns=2, enable_strip_stage_directions=True)
    result = compress(messages, config)

    # The very last user message in the fixture is turn index 5 (0-based),
    # which must be within the most recent 2 turns and therefore untouched.
    last_user_msgs = [m["content"] for m in result if m.get("role") == "user"]
    assert any("（走进房间）第5次台词" in c for c in last_user_msgs)


def test_footer_duplicates_collapsed_to_one():
    messages = build_fixture_messages()
    config = CompressorConfig(keep_recent_turns=2)
    result = compress(messages, config)
    footer_occurrences = sum(1 for m in result if m.get("content") == FOOTER)
    assert footer_occurrences == 1


def test_default_config_never_crashes_on_minimal_input():
    messages = [{"role": "user", "content": "hi"}]
    result = compress(messages)
    assert result == [{"role": "user", "content": "hi"}]


def test_recurring_footer_survives_even_when_every_copy_is_in_pruned_turns():
    """Regression test for a real bug found 2026-07-25 via a live DeepSeek
    A/B call: a footer/format-reminder repeated across every turn got
    deduped down to one copy, but that copy landed in a turn
    history_window then classified as "old" and dropped in trim mode —
    silently deleting the only remaining copy of a mandatory reply-format
    instruction. The reply generated from the compressed request visibly
    skipped the format the uncompressed request's reply followed.

    Reordering dedupe vs. history_window alone does NOT fix this — a
    recurring instruction can legitimately live only in turns older than
    the keep window (verified empirically after first trying that fix and
    still reproducing the missing footer). The actual fix: snapshot which
    system messages recur across 2+ turns *before* any pruning runs, and
    if every copy of a recurring text is gone after compression, re-attach
    one copy at the end — mirroring how qqbot's own build_reply() appends
    its footer fresh, as the final message, on every real call rather than
    relying on it surviving from stored history.
    """
    footer = "[FORMAT RULE] End every reply with a mood tag."
    messages = [{"role": "system", "content": "persona"}]
    for i in range(4):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})
        messages.append({"role": "system", "content": footer})
    messages.append({"role": "user", "content": "final pending question"})

    config = CompressorConfig(keep_recent_turns=1, history_window_mode="trim")
    result = compress(messages, config)

    footer_occurrences = sum(1 for m in result if m.get("content") == footer)
    assert footer_occurrences == 1, (
        "a recurring instruction must survive exactly once, even if every "
        "turn that originally carried it gets pruned by history_window"
    )
    # and it must land after the pending question, not before it
    assert result[-1] == {"role": "system", "content": footer}
