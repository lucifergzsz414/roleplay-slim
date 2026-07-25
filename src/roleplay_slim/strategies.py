"""Compression strategies applied to the dynamic (non-prefix) region.

Every strategy here is rule-based — no ML model, no scoring, no risk of a
compression model quietly rewriting a character's dialogue. That's a
deliberate v0.1 boundary: cheap, fast, and the failure modes are easy to
reason about (a bad regex either matches too much or too little; it never
"hallucinates" a summary).
"""
from __future__ import annotations

import re

from .config import CompressorConfig
from .segmenter import Turn

_WHITESPACE_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")


def whitespace_normalize(text: str) -> str:
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _WHITESPACE_RUN_RE.sub("\n\n", text)
    return text.strip()


_ISO_TIMESTAMP_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b"
)


def normalize_prefix_timestamps(text: str, bucket_minutes: int) -> str:
    """Round ISO-8601 timestamps down to the nearest `bucket_minutes`
    boundary (e.g. 14:23:47 -> 14:20:00 with bucket_minutes=5).

    This exists for apps that embed a live timestamp inside the prefix
    (persona/instructions) region — otherwise that timestamp changes every
    request and defeats the provider's prefix cache regardless of anything
    else roleplay-slim does, since the prefix is never touched by default.

    Deliberately *not* a placeholder-style replacement (swap the timestamp
    for an opaque token like Kompact's cache aligner does): a roleplay
    persona often genuinely needs to know roughly what time it is to react
    in character, so this keeps real, still-useful time-of-day information
    instead of discarding it. bucket_minutes=0 is a no-op.
    """
    if bucket_minutes <= 0:
        return text

    def _bucket(match: re.Match) -> str:
        date_part, hour, minute, _second, tz = match.groups()
        bucketed_minute = (int(minute) // bucket_minutes) * bucket_minutes
        return f"{date_part}T{int(hour):02d}:{bucketed_minute:02d}:00{tz or ''}"

    return _ISO_TIMESTAMP_RE.sub(_bucket, text)


def content_key(content) -> str:
    """A hashable, comparable identity for a message's `content` field.
    OpenAI's vision API allows `content` to be a list of parts (text +
    image_url blocks) instead of a plain string — real apps use this (seen
    in a real third-party app's own message-building code, which branches
    on `isinstance(content, list)`). Lists aren't hashable, so anything
    that needs to compare/dedupe content by equality (this function's
    callers) needs a stable string key instead of the raw value."""
    if isinstance(content, str):
        return content
    import json

    return json.dumps(content, sort_keys=True, default=str)


def dedupe_verbatim_tail(turns: list[Turn]) -> list[Turn]:
    """If the exact same system-message text appears more than once across
    the dynamic region (e.g. a format-reminder footer repeated every
    request), keep only its last occurrence."""
    seen_system_texts: set[str] = set()
    # Walk backwards so the *last* occurrence of each duplicate wins, then
    # rebuild turns in original order with earlier duplicates dropped.
    keep_flags: list[list[bool]] = [[True] * len(t.messages) for t in turns]
    for ti in range(len(turns) - 1, -1, -1):
        for mi in range(len(turns[ti].messages) - 1, -1, -1):
            m = turns[ti].messages[mi]
            if m.get("role") != "system":
                continue
            key = content_key(m.get("content", ""))
            if key in seen_system_texts:
                keep_flags[ti][mi] = False
            else:
                seen_system_texts.add(key)

    new_turns: list[Turn] = []
    for ti, turn in enumerate(turns):
        kept = [m for mi, m in enumerate(turn.messages) if keep_flags[ti][mi]]
        if kept:
            new_turns.append(Turn(kept))
    return new_turns


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？…\.\!\?])\s*")


def _extractive_trim(text: str) -> str:
    """Keep the first and last sentence, drop the middle — a cheap
    fallback for apps with no memory/summary layer of their own."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if len(sentences) <= 2:
        return text
    return sentences[0] + " …… " + sentences[-1]


def history_window(turns: list[Turn], config: CompressorConfig) -> list[Turn]:
    """Leave the most recent `keep_recent_turns` turns untouched. Older
    turns are dropped or trimmed depending on config.history_window_mode."""
    keep_n = max(0, config.keep_recent_turns)
    if len(turns) <= keep_n:
        return turns

    cutoff = len(turns) - keep_n
    old_turns, recent_turns = turns[:cutoff], turns[cutoff:]

    if config.history_window_mode == "drop":
        return recent_turns

    trimmed: list[Turn] = []
    for turn in old_turns:
        new_messages = []
        for m in turn.messages:
            if m.get("role") in ("user", "assistant"):
                content = m.get("content", "")
                # Multimodal content (a list of text/image_url parts, per
                # the OpenAI vision format) isn't plain text — trimming it
                # with sentence-boundary regex would crash or corrupt an
                # image reference, so leave it untouched rather than guess.
                if isinstance(content, str):
                    new_messages.append({**m, "content": _extractive_trim(content)})
                else:
                    new_messages.append(m)
            # Older system messages (footers, memory blocks) in dropped
            # turns carry little standalone value once the turn itself has
            # been trimmed — drop them here; dedupe_verbatim_tail already
            # ran before this in the pipeline for the ones worth keeping.
        if new_messages:
            trimmed.append(Turn(new_messages))
    return trimmed + recent_turns


def strip_stage_directions(turns: list[Turn], config: CompressorConfig, keep_recent: int) -> list[Turn]:
    """Remove parenthetical stage directions (per config.stage_direction_pattern)
    from turns older than the most recent `keep_recent`, keeping the actual
    dialogue text intact. Recent turns are left fully alone — nuance matters
    most in what's just been said."""
    pattern = re.compile(config.stage_direction_pattern)
    cutoff = max(0, len(turns) - keep_recent)
    new_turns: list[Turn] = []
    for i, turn in enumerate(turns):
        if i >= cutoff:
            new_turns.append(turn)
            continue
        new_messages = []
        for m in turn.messages:
            content = m.get("content", "")
            if m.get("role") in ("user", "assistant") and isinstance(content, str):
                stripped = pattern.sub("", content)
                stripped = whitespace_normalize(stripped)
                if stripped:
                    new_messages.append({**m, "content": stripped})
            else:
                # Non-string (multimodal) content passes through untouched
                # — see the history_window comment above for why.
                new_messages.append(m)
        if new_messages:
            new_turns.append(Turn(new_messages))
    return new_turns
