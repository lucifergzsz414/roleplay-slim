"""Top-level entry point: compress(messages, config) -> messages."""
from __future__ import annotations

import logging

from .config import CompressorConfig
from .segmenter import Turn, segment
from .stats import estimate_messages_tokens
from .strategies import (
    content_key,
    dedupe_verbatim_tail,
    history_window,
    normalize_prefix_timestamps,
    strip_stage_directions,
    whitespace_normalize,
)

logger = logging.getLogger("roleplay_slim")


def _turns_to_messages(turns: list[Turn]) -> list[dict]:
    out: list[dict] = []
    for turn in turns:
        out.extend(turn.messages)
    return out


def _enforce_token_budget(
    prefix: list[dict], turns: list[Turn], config: CompressorConfig
) -> list[Turn]:
    """Drop the oldest turns until the estimated prompt fits
    config.max_prompt_tokens.

    Whole turns are dropped rather than individual messages. A turn is cut
    at each new user message, so an assistant tool_calls message and the
    tool results answering it always live inside the same turn — dropping
    per-message could strand a tool_calls block with no matching results,
    which providers reject outright.

    Two things are never sacrificed to the budget: the cache-stable prefix
    (the entire premise of this library) and the most recent
    budget_min_recent_turns turns (dropping the pending question deletes
    the request itself). A budget too small to fit those is reported and
    left unmet — returning a structurally broken prompt would be a worse
    outcome than returning an honest oversized one.
    """
    budget = config.max_prompt_tokens
    if budget is None:
        return turns

    floor = max(1, config.budget_min_recent_turns)
    prefix_tokens = estimate_messages_tokens(prefix)

    while len(turns) > floor:
        if prefix_tokens + estimate_messages_tokens(_turns_to_messages(turns)) <= budget:
            return turns
        turns = turns[1:]

    total = prefix_tokens + estimate_messages_tokens(_turns_to_messages(turns))
    if total > budget:
        logger.warning(
            "max_prompt_tokens=%d could not be met: ~%d tokens remain after dropping "
            "history down to the last %d turn(s) (prefix alone is ~%d). The prefix and "
            "the most recent turns are never dropped — raise the budget, or shorten the "
            "prefix if it is the bulk of it.",
            budget,
            total,
            len(turns),
            prefix_tokens,
        )
    return turns


def _find_recurring_system_texts(turns: list[Turn]) -> dict[str, dict]:
    """System/developer messages whose content appears in 2+ of the
    original turns read as a recurring per-request instruction (a
    footer/format reminder), not a one-off note — they must not be
    silently lost even if every turn that happened to carry a copy gets
    pruned away.

    The *last* occurrence of each distinct content is stored in full
    (preserving name, cache_control, and any other provider-specific
    metadata), not just the content string — the re-attached copy should
    be a faithful restoration of the original message, not a bare
    ``{"role": "system", "content": "..."}`` skeleton.

    Keyed by content_key() rather than the raw content since content can
    be a list (OpenAI-style multimodal parts), which isn't hashable."""
    counts: dict[str, int] = {}
    last_occurrence: dict[str, dict] = {}
    for turn in turns:
        for m in turn.messages:
            if m.get("role") in ("system", "developer"):
                content = m.get("content", "")
                key = content_key(content)
                counts[key] = counts.get(key, 0) + 1
                last_occurrence[key] = dict(m)
    return {key: last_occurrence[key] for key, n in counts.items() if n >= 2}


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

    # Off by default. The prefix is otherwise guaranteed byte-for-byte
    # untouched (see the docstring above) — this is the one opt-in
    # exception, for apps whose prefix embeds a live timestamp that would
    # otherwise defeat the provider's cache on every single request no
    # matter what else this library does.
    if config.enable_prefix_normalize:
        prefix = [
            {
                **m,
                "content": normalize_prefix_timestamps(
                    m["content"], config.prefix_timestamp_bucket_minutes
                ),
            }
            if m.get("role") == "system" and isinstance(m.get("content"), str)
            else m
            for m in prefix
        ]

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
    # every copy got pruned away — mirroring how chat apps typically append
    # their own footer fresh, as the last message, on every real call.
    recurring_system_texts = _find_recurring_system_texts(turns)

    if config.enable_history_window:
        turns = history_window(turns, config)

    if config.enable_dedupe_verbatim_tail:
        turns = dedupe_verbatim_tail(turns)

    if config.enable_strip_stage_directions:
        turns = strip_stage_directions(turns, config, keep_recent=config.keep_recent_turns)

    # Runs last among the turn-level passes, so it measures what the other
    # strategies actually produced rather than guessing ahead of them — and
    # deliberately *before* the recurring-system re-attachment below, so a
    # mandatory format instruction that budget pruning just removed still
    # gets restored (the same failure mode history_window hit on 2026-07-25).
    turns = _enforce_token_budget(prefix, turns, config)

    dynamic = _turns_to_messages(turns)

    surviving_keys = {content_key(m.get("content", "")) for m in dynamic if m.get("role") in ("system", "developer")}
    for key, msg in recurring_system_texts.items():
        if key not in surviving_keys:
            dynamic.append(dict(msg))

    if config.enable_whitespace_normalize:
        dynamic = [
            {**m, "content": whitespace_normalize(m["content"])}
            if m.get("content") and isinstance(m["content"], str)
            else m
            for m in dynamic
        ]

    return prefix + dynamic
