"""Splits an OpenAI-format messages array into a cache-stable prefix and a
dynamic region, and further splits the dynamic region into discrete
user/assistant turns.

This is the part that makes roleplay-slim different from a generic text
compressor: it knows that most chat apps front-load a fixed persona/system
block that the provider's own prompt cache (e.g. DeepSeek's disk cache)
rewards for staying byte-identical across requests, and that compressing
that block would silently defeat the provider's caching. Everything after
that point is paid for in full on every single request, which is where
compression actually has value.
"""
from __future__ import annotations

from dataclasses import dataclass

Message = dict  # {"role": str, "content": str}


def detect_prefix_length(messages: list[Message], override: int | None = None) -> int:
    """Return how many leading messages form the cache-stable prefix.

    Default heuristic: every leading message with role == "system", up to
    (but not including) the first "user" or "assistant" message. Most chat
    apps put their fixed persona/instructions here and start the actual
    conversation afterward, so this needs no per-app configuration to be
    useful out of the box.
    """
    if override is not None:
        return max(0, min(override, len(messages)))
    n = 0
    for m in messages:
        if m.get("role") == "system":
            n += 1
        else:
            break
    return n


@dataclass
class Turn:
    """One user message plus the assistant message that answered it (if
    any). Trailing system messages injected between turns (footers,
    reminders, memory blocks) are attached to whichever turn precedes them,
    since that's the request they were generated for."""

    messages: list[Message]

    @property
    def has_dialogue(self) -> bool:
        return any(m.get("role") in ("user", "assistant") for m in self.messages)


def split_into_turns(dynamic_messages: list[Message]) -> list[Turn]:
    """Group the dynamic region into turns, breaking right before each new
    "user" message (a fresh turn always starts with the human speaking)."""
    turns: list[Turn] = []
    current: list[Message] = []
    for m in dynamic_messages:
        if m.get("role") == "user" and current:
            turns.append(Turn(current))
            current = []
        current.append(m)
    if current:
        turns.append(Turn(current))
    return turns


def segment(messages: list[Message], prefix_override: int | None = None) -> tuple[list[Message], list[Turn]]:
    """Split messages into (prefix, turns). prefix is returned untouched —
    callers must never modify it if they want to preserve upstream prompt
    caching."""
    n = detect_prefix_length(messages, prefix_override)
    prefix = messages[:n]
    dynamic = messages[n:]
    return prefix, split_into_turns(dynamic)
