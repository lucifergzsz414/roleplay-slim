"""Compression for the Anthropic Messages API shape (`/v1/messages`).

This is deliberately a separate module from `compressor.py`/`strategies.py`,
not an extension of them. Anthropic's request shape differs from OpenAI's in
ways that make sharing code more dangerous than duplicating a small amount
of it:

  - `system` is a top-level field, never a message in `messages` — so there
    is no "leading system messages" prefix to detect; the whole `messages`
    array is the dynamic region.
  - `content` is *always* a block array (`text` / `tool_use` / `tool_result`
    / `image`), never a plain string. Every OpenAI-side strategy that reads
    `content` as (or falls back to) a string would need a parallel branch
    here, and mixing that logic into the same functions risks the OpenAI
    path accidentally picking up Anthropic-shaped assumptions.

Per docs/designs/anthropic-protocol-support.md's validation: a hand-run
synthetic benchmark showed the dominant compression lever for this shape is
*not* text dedup (single-digit % — reminder-block duplication is a small
share of total size) but trimming old `tool_result` content (67-85% on
10-30 turn synthetic conversations) — a real Read/Bash tool output sitting
in an old turn costs the same on every request as the day it was produced,
long after its detail has stopped mattering. So this module intentionally
ships **only** that lever for now (the Anthropic equivalent of
`history_window`'s trim mode) rather than porting every OpenAI strategy —
see the design doc's "验证" section for why that scope cut is deliberate,
not a shortcut.

`segmenter.split_into_turns` is reused as-is: it only reads `m.get("role")`
and never touches `content`, so it works unmodified on Anthropic-shaped
messages (which only ever carry "user"/"assistant" roles).
"""
from __future__ import annotations

from .segmenter import split_into_turns

_TOOL_RESULT_PLACEHOLDER = "[older tool output omitted by roleplay-slim]"


def _block_text_len(block: dict) -> int:
    """Rough size of one content block, for before/after accounting.
    Mirrors stats.py's philosophy (approximate, char-based when tiktoken
    isn't warranted for a quick estimate) but must also count tool_use/
    tool_result, which stats.py's OpenAI-side `_text_of` deliberately does
    not (those block types don't exist on the OpenAI side)."""
    if not isinstance(block, dict):
        return 0
    btype = block.get("type")
    if btype == "text":
        return len(str(block.get("text", "")))
    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, str):
            return len(content)
        # tool_result content can itself be a block list (rare, but valid)
        return sum(_block_text_len(b) for b in content) if isinstance(content, list) else 0
    if btype == "tool_use":
        import json

        return len(json.dumps(block.get("input", {})))
    return 0


def estimate_anthropic_messages_chars(messages: list[dict]) -> int:
    """Char-based size estimate across an Anthropic-shaped messages array.
    Used for before/after accounting the same way stats.py's
    estimate_messages_tokens is used on the OpenAI side — kept separate
    (see module docstring) rather than teaching that function this shape."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(_block_text_len(b) for b in content)
    return total


def trim_old_tool_results(messages: list[dict], keep_recent_turns: int) -> list[dict]:
    """Replace `tool_result` block content in every turn older than the most
    recent `keep_recent_turns` with a short placeholder. Everything else
    (text blocks, tool_use blocks, and all content within the kept recent
    turns) passes through untouched.

    A "turn" here is the same unit `segmenter.split_into_turns` already
    defines: starts at a `user` message, includes everything up to (not
    including) the next `user` message. For a tool round trip that means a
    turn can contain more than 2 messages (user question -> assistant
    tool_use -> user tool_result -> ... -> assistant final reply all before
    the next real user message), which is exactly the shape whose tool
    output this function targets.
    """
    if keep_recent_turns <= 0:
        turns = split_into_turns(messages)
        keep_from = len(turns)
    else:
        turns = split_into_turns(messages)
        keep_from = max(0, len(turns) - keep_recent_turns)

    out: list[dict] = []
    for i, turn in enumerate(turns):
        if i >= keep_from:
            out.extend(turn.messages)
            continue
        for m in turn.messages:
            content = m.get("content")
            if not isinstance(content, list):
                out.append(m)
                continue
            new_content = []
            changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    new_content.append({**block, "content": _TOOL_RESULT_PLACEHOLDER})
                    changed = True
                else:
                    new_content.append(block)
            out.append({**m, "content": new_content} if changed else m)
    return out


def compress_anthropic_messages(messages: list[dict], keep_recent_turns: int) -> list[dict]:
    """Entry point mirroring `compressor.compress`'s role, scoped to the one
    strategy this module currently ships. `system` is not a parameter here
    on purpose — callers must not pass it through this function, since it
    is never part of `messages` in the Anthropic shape and must be
    forwarded upstream untouched regardless of what this does."""
    return trim_old_tool_results(messages, keep_recent_turns)


__all__ = [
    "compress_anthropic_messages",
    "estimate_anthropic_messages_chars",
    "trim_old_tool_results",
]
