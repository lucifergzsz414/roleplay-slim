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

    if config.enable_dedupe_verbatim_tail:
        turns = dedupe_verbatim_tail(turns)

    if config.enable_history_window:
        turns = history_window(turns, config)

    if config.enable_strip_stage_directions:
        turns = strip_stage_directions(turns, config, keep_recent=config.keep_recent_turns)

    dynamic = _turns_to_messages(turns)

    if config.enable_whitespace_normalize:
        dynamic = [
            {**m, "content": whitespace_normalize(m.get("content", ""))} if m.get("content") else m
            for m in dynamic
        ]

    return prefix + dynamic
