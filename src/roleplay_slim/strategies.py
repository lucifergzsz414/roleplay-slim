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
            content = m.get("content", "")
            if content in seen_system_texts:
                keep_flags[ti][mi] = False
            else:
                seen_system_texts.add(content)

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
                new_messages.append({**m, "content": _extractive_trim(m.get("content", ""))})
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
            if m.get("role") in ("user", "assistant"):
                stripped = pattern.sub("", m.get("content", ""))
                stripped = whitespace_normalize(stripped)
                if stripped:
                    new_messages.append({**m, "content": stripped})
            else:
                new_messages.append(m)
        if new_messages:
            new_turns.append(Turn(new_messages))
    return new_turns
