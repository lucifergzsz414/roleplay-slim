"""Rough token accounting for before/after comparisons.

Uses tiktoken when it's installed (accurate for OpenAI-family tokenizers,
a reasonable proxy for DeepSeek/most others); falls back to a char/4
estimate otherwise so the library has no hard dependency on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_ENC: Any = None
try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - exercised only when tiktoken is absent
    pass


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text) // 4)


def _text_of(content) -> str:
    """Extract the text portion of a message's content field. OpenAI's
    vision API allows content to be a list of {"type": "text"|"image_url",
    ...} parts instead of a plain string; only the text parts have a
    meaningful token count here — an image's real cost depends on
    provider-specific pixel/tile accounting this library doesn't attempt
    to model, so it's simply not counted rather than guessed at."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return ""


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(_text_of(m.get("content", ""))) for m in messages)


@dataclass
class CompressionStats:
    request_count: int = 0
    tokens_before_total: int = 0
    tokens_after_total: int = 0
    history: list[dict] = field(default_factory=list)

    def record(self, before: list[dict], after: list[dict]) -> dict:
        before_tokens = estimate_messages_tokens(before)
        after_tokens = estimate_messages_tokens(after)
        self.request_count += 1
        self.tokens_before_total += before_tokens
        self.tokens_after_total += after_tokens
        entry = {
            "tokens_before": before_tokens,
            "tokens_after": after_tokens,
            "saved": before_tokens - after_tokens,
        }
        self.history.append(entry)
        if len(self.history) > 200:
            del self.history[: len(self.history) - 200]
        return entry

    def summary(self) -> dict:
        saved = self.tokens_before_total - self.tokens_after_total
        pct = (saved / self.tokens_before_total * 100) if self.tokens_before_total else 0.0
        return {
            "request_count": self.request_count,
            "tokens_before_total": self.tokens_before_total,
            "tokens_after_total": self.tokens_after_total,
            "tokens_saved_total": saved,
            "savings_pct": round(pct, 2),
        }
