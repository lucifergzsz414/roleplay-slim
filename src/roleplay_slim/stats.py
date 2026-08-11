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


def _coerce_int(value: Any) -> int | None:
    """Read one numeric field out of an upstream `usage` block.

    Providers are inconsistent here — a field can be absent, null, or (on
    some OpenAI-compatible gateways) a float. Anything that isn't a real
    number is reported as "not present" rather than guessed at, because a
    wrong number here is worse than a missing one: these are the figures
    that back the project's cache-preservation claim. bool is excluded
    explicitly since it's an int subclass in Python.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


@dataclass
class CompressionStats:
    request_count: int = 0
    tokens_before_total: int = 0
    tokens_after_total: int = 0
    history: list[dict] = field(default_factory=list)

    # Real token counts reported by the upstream provider, as opposed to
    # the tiktoken/char-based estimates above. These matter more than the
    # estimates: cl100k_base is an OpenAI tokenizer being used as a stand-in
    # for whatever the upstream actually runs, whereas these numbers come
    # from the provider's own accounting. Only populated for non-streaming
    # responses (a streamed response only carries usage when the caller
    # sets stream_options.include_usage, which most don't).
    usage_sample_count: int = 0
    upstream_prompt_tokens_total: int = 0
    upstream_completion_tokens_total: int = 0
    # DeepSeek-style prefix-cache accounting. Absent on providers that
    # don't report it, which is why cache_sample_count is tracked
    # separately from usage_sample_count — a provider can report
    # prompt_tokens without reporting any cache breakdown at all.
    cache_sample_count: int = 0
    cache_hit_tokens_total: int = 0
    cache_miss_tokens_total: int = 0

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

    def record_usage(self, usage: Any) -> dict | None:
        """Record the `usage` block from an upstream response.

        Returns the parsed figures, or None if `usage` carried nothing
        recognisable. Deliberately total-tolerant: this is best-effort
        telemetry sitting in the path of a live proxy request, so a
        provider returning an unexpected shape must never raise.
        """
        if not isinstance(usage, dict):
            return None

        prompt = _coerce_int(usage.get("prompt_tokens"))
        completion = _coerce_int(usage.get("completion_tokens"))
        if prompt is None and completion is None:
            return None

        self.usage_sample_count += 1
        self.upstream_prompt_tokens_total += prompt or 0
        self.upstream_completion_tokens_total += completion or 0

        hit = _coerce_int(usage.get("prompt_cache_hit_tokens"))
        miss = _coerce_int(usage.get("prompt_cache_miss_tokens"))
        if hit is not None or miss is not None:
            self.cache_sample_count += 1
            self.cache_hit_tokens_total += hit or 0
            self.cache_miss_tokens_total += miss or 0

        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        }

    def upstream_summary(self) -> dict | None:
        """Provider-reported figures, or None when nothing has been
        recorded yet. Returning None rather than a block of zeroes is
        deliberate — "no measurement" and "measured zero" mean very
        different things when the point of this block is to be evidence."""
        if self.usage_sample_count == 0:
            return None

        out: dict = {
            "usage_sample_count": self.usage_sample_count,
            "prompt_tokens_total": self.upstream_prompt_tokens_total,
            "completion_tokens_total": self.upstream_completion_tokens_total,
            "cache_hit_tokens_total": None,
            "cache_miss_tokens_total": None,
            "cache_hit_pct": None,
        }
        if self.cache_sample_count:
            counted = self.cache_hit_tokens_total + self.cache_miss_tokens_total
            out["cache_hit_tokens_total"] = self.cache_hit_tokens_total
            out["cache_miss_tokens_total"] = self.cache_miss_tokens_total
            out["cache_hit_pct"] = (
                round(self.cache_hit_tokens_total / counted * 100, 2) if counted else 0.0
            )
        return out

    def summary(self) -> dict:
        saved = self.tokens_before_total - self.tokens_after_total
        pct = (saved / self.tokens_before_total * 100) if self.tokens_before_total else 0.0
        return {
            "request_count": self.request_count,
            "tokens_before_total": self.tokens_before_total,
            "tokens_after_total": self.tokens_after_total,
            "tokens_saved_total": saved,
            "savings_pct": round(pct, 2),
            # Estimated (tiktoken/char-based) vs. what the provider itself
            # reported — see upstream_summary().
            "upstream": self.upstream_summary(),
        }
