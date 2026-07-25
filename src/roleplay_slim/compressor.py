"""Top-level entry point: compress(messages, config) -> messages."""
from __future__ import annotations

from .config import CompressorConfig
from .segmenter import Turn, segment
from .strategies import dedupe_verbatim_tail, history_window, strip_stage_directions, whitespace_normalize


def _turns_to_messages(turns: list[Turn]) -> list[dict]:
    out: list[dict] = []
    for turn in turns:
        out.extend(turn.messages)
    return out


def _find_recurring_system_texts(turns: list[Turn]) -> set[str]:
    """System-message content that appears in 2+ of the original turns
    reads as a recurring per-request instruction (a footer/format
    reminder), not a one-off note — it must not be silently lost even if
    every turn that happened to carry a copy gets pruned away."""
    counts: dict[str, int] = {}
    for turn in turns:
        for m in turn.messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                counts[content] = counts.get(content, 0) + 1
    return {text for text, n in counts.items() if n >= 2}


def compress(messages: list[dict], config: CompressorConfig | None = None) -> list[dict]:
    """Compress an OpenAI-format messages array.

    The leading cache-stable prefix (see segmenter.detect_prefix_length) is
    always returned byte-for-byte unmodified — that's the whole point: it's
    the part the upstream provider's own prompt cache rewards for staying
    identical across requests, so re-compressing it differently every call
    would silently defeat that caching for no benefit.
    """
    config = config or CompressorConfig()
    prefix, turns = segment(messages, config.prefix_override)

    # Snapshot which system messages recur across turns *before* any
    # pruning — real bug found 2026-07-25 via a live DeepSeek A/B call: a
    # footer/format-reminder repeated across every turn could get deduped
    # down to a copy that then landed in a turn history_window classified
    # as "old" and dropped — silently deleting a mandatory reply-format
    # instruction. The compressed request's reply visibly skipped the
    # format the uncompressed one followed. Reordering dedupe vs.
    # history_window alone doesn't fix this (a recurring instruction can
    # legitimately live only in turns older than the keep window), so
    # instead: remember what recurs, and re-attach one copy at the end if
    # every copy got pruned away — matching how qqbot's own build_reply()
    # appends its footer fresh, as the last message, on every call.
    recurring_system_texts = _find_recurring_system_texts(turns)

    if config.enable_history_window:
        turns = history_window(turns, config)

    if config.enable_dedupe_verbatim_tail:
        turns = dedupe_verbatim_tail(turns)

    if config.enable_strip_stage_directions:
        turns = strip_stage_directions(turns, config, keep_recent=config.keep_recent_turns)

    dynamic = _turns_to_messages(turns)

    surviving_system_texts = {m.get("content", "") for m in dynamic if m.get("role") == "system"}
    for text in recurring_system_texts:
        if text not in surviving_system_texts:
            dynamic.append({"role": "system", "content": text})

    if config.enable_whitespace_normalize:
        dynamic = [
            {**m, "content": whitespace_normalize(m.get("content", ""))} if m.get("content") else m
            for m in dynamic
        ]

    return prefix + dynamic
