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

Anthropic tool callbacks also use `role="user"`, so this module keeps its own
small turn splitter: tool-result-only messages stay with the human request
that initiated them, while the next user text/image message starts a new turn.
"""
from __future__ import annotations

import json

from .stats import estimate_tokens

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


def _block_token_count(block: dict) -> int:
    if not isinstance(block, dict):
        return 0
    btype = block.get("type")
    if btype == "text":
        return estimate_tokens(str(block.get("text", "")))
    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, str):
            return estimate_tokens(content)
        if isinstance(content, list):
            return sum(_block_token_count(item) for item in content)
        return 0
    if btype == "tool_use":
        payload = json.dumps(block.get("input", {}), ensure_ascii=False)
        return estimate_tokens(payload)
    return 0


def estimate_anthropic_messages_tokens(messages: list[dict]) -> int:
    """Estimate Anthropic message content in the same token unit as OpenAI."""
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            total += sum(_block_token_count(block) for block in content)
    return total


def _is_tool_result_message(message: dict) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    )


def _split_anthropic_turns(messages: list[dict]) -> list[list[dict]]:
    """Group tool-result callbacks with the human turn that initiated them."""
    turns: list[list[dict]] = []
    for message in messages:
        starts_human_turn = (
            message.get("role") == "user" and not _is_tool_result_message(message)
        )
        if not turns or starts_human_turn:
            turns.append([])
        turns[-1].append(message)
    return turns


def trim_old_tool_results(messages: list[dict], keep_recent_turns: int) -> list[dict]:
    """Replace `tool_result` block content in every turn older than the most
    recent `keep_recent_turns` with a short placeholder. Everything else
    (text blocks, tool_use blocks, and all content within the kept recent
    turns) passes through untouched.

    A turn starts at a human `user` message. `user` messages containing only
    `tool_result` blocks remain in that same turn, including chained tool
    calls, until the next user text/image message begins a new human turn.
    """
    if keep_recent_turns <= 0:
        turns = _split_anthropic_turns(messages)
        keep_from = len(turns)
    else:
        turns = _split_anthropic_turns(messages)
        keep_from = max(0, len(turns) - keep_recent_turns)

    out: list[dict] = []
    for i, turn in enumerate(turns):
        if i >= keep_from:
            out.extend(turn)
            continue
        for m in turn:
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
    "estimate_anthropic_messages_tokens",
    "trim_old_tool_results",
]
