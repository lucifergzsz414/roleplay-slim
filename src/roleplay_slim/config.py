"""Configuration model for roleplay-slim.

A CompressorConfig controls how much of the "dynamic" region (everything
after the leading system-message prefix) gets compressed, and how. Every
field has a safe default so ``CompressorConfig()`` alone is usable.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# Named regex presets for stage_direction_pattern, covering the
# conventions seen across the real apps this project was validated
# against (a production QQ roleplay bot uses full-width parens; a noir
# fixture used asterisks) plus a couple of other common ones. Pass either
# a preset name (looked up here) or your own raw regex string.
STAGE_DIRECTION_PRESETS: dict[str, str] = {
    "fullwidth_parens": r"（[^）]*）",  # e.g. （挥手）— common in Chinese roleplay bots
    "halfwidth_parens": r"\([^)]*\)",  # (waves)
    "asterisk": r"(?<!\*)\*[^*]+\*(?!\*)",  # *waves* — uses lookaround to
    # avoid matching **bold** or ***bold-italic*** (markdown emphasis).
    # Note: will still match *italic* single-asterisk emphasis — if your
    # app uses markdown formatting in dialogue, prefer a distinct convention
    # for stage directions ("fullwidth_parens" / "halfwidth_parens" /
    # "square_bracket") and avoid this preset.
    "square_bracket": r"\[[^\]]*\]",  # [waves] — careful: matches format-tag
    # brackets like [信任:+0] too if you use those; prefer a distinct
    # convention for stage directions if your app also uses bracket tags.
}


@dataclass
class CompressorConfig:
    # How many of the most recent user/assistant turn-pairs are left
    # completely untouched. Everything older than this window is a
    # candidate for history_window / strip_stage_directions.
    keep_recent_turns: int = 6

    # Master switches for each strategy. whitespace_normalize and
    # dedupe_verbatim_tail are cheap and safe, so they default on.
    # strip_stage_directions is format-sensitive (depends on the app's own
    # bracket convention) so it defaults off until the caller supplies a
    # pattern that actually matches their content.
    enable_whitespace_normalize: bool = True
    enable_dedupe_verbatim_tail: bool = True
    enable_history_window: bool = True
    enable_strip_stage_directions: bool = False

    # What happens to turns older than keep_recent_turns when
    # enable_history_window is on: "drop" removes them outright (appropriate
    # for apps that already inject a
    # separate persistent-memory summary elsewhere); "trim" keeps a short
    # extractive stub (first + last sentence) instead of dropping entirely;
    # "summarize" hands them to the `summarizer` callback below and replaces
    # the whole block with its single returned message.
    history_window_mode: str = "trim"  # "trim" | "drop" | "summarize"

    # Optional callback that condenses the aged-out portion of the history.
    # Receives the messages of every turn older than keep_recent_turns and
    # returns one string, which replaces that entire block as a single
    # system message. Only consulted when history_window_mode ==
    # "summarize".
    #
    # This is the library's one deliberate escape hatch from being purely
    # rule-based. The rules here are cheap and predictable but blunt — the
    # extractive "trim" keeps the first and last sentence and does nothing
    # at all to text with no sentence-ending punctuation, which is most
    # short chat lines. Rather than take on an ML dependency and start
    # rewriting characters' dialogue, the caller supplies the condensing:
    # apps in this space usually already have a memory/summary layer whose
    # output is far better than anything a regex could produce.
    #
    # Python-only by nature (a callable cannot come from TOML). Returning
    # an empty string means "this history isn't worth keeping" and drops
    # the block. Raising is survivable: the failure is logged and the
    # block falls back to "trim" — this callback is usually a network call
    # to an LLM, and a proxy handling a live request must not fail just
    # because the summarizer timed out.
    summarizer: Callable[[list[dict]], str] | None = None

    # Regex describing how this app wraps stage directions / action
    # descriptions. Either a raw regex string (e.g. r"（[^）]*）") or the
    # name of a STAGE_DIRECTION_PRESETS entry (e.g. "fullwidth_parens"),
    # resolved in __post_init__. Only consulted when
    # enable_strip_stage_directions is True.
    stage_direction_pattern: str = "fullwidth_parens"

    # Optional hard ceiling on the estimated token count of the whole
    # compressed prompt. None (the default) leaves budgeting off entirely,
    # so existing configs behave byte-for-byte as before.
    #
    # Why this exists: every other setting here is *structural* — "keep the
    # last 6 turns" says nothing about how big those turns are. A history
    # of 200 long turns trimmed to first+last sentence each is still an
    # enormous prompt, and trimming does nothing at all to messages with no
    # sentence-ending punctuation (short chat lines like "ok" / "在吗").
    # Without a budget, the compressed output has no upper bound. With one,
    # the oldest turns are dropped outright until the estimate fits.
    #
    # Enforced on a best-effort basis: the cache-stable prefix is never
    # touched and the most recent budget_min_recent_turns turns are never
    # dropped, so a budget smaller than those two combined is logged as a
    # warning and left unmet rather than honoured by breaking the prompt.
    max_prompt_tokens: int | None = None

    # Floor for budget enforcement: however far over budget the prompt is,
    # at least this many of the most recent turns always survive. Dropping
    # the final turn would delete the very question being asked, which is
    # never a useful way to save tokens.
    budget_min_recent_turns: int = 1

    # Override the auto-detected prefix length (see segmenter.detect_prefix).
    # None means "auto-detect: all leading system messages before the first
    # user/assistant message". Set an explicit int only when the app's
    # cache-stable prefix doesn't match that heuristic.
    prefix_override: int | None = None

    # Off by default: the prefix is otherwise guaranteed byte-for-byte
    # untouched. Turn this on only if your own prefix embeds a live
    # timestamp (defeating the provider's cache on every single request
    # regardless of anything else this library does) — it rounds ISO-8601
    # timestamps found in the prefix down to the nearest
    # prefix_timestamp_bucket_minutes boundary instead of leaving them exact.
    enable_prefix_normalize: bool = False
    prefix_timestamp_bucket_minutes: int = 5

    # Compiled once here in __post_init__ rather than by every
    # strip_stage_directions() call — a compress() call only calls it once,
    # so this isn't a hot-loop fix, but there's no reason to pay for it
    # more than once per config either. Not a dataclass field: excluded
    # from __init__/repr/eq so it doesn't change the public constructor.
    _compiled_stage_direction_pattern: re.Pattern[str] | None = field(
        init=False, repr=False, compare=False, default=None
    )

    def __post_init__(self) -> None:
        if self.keep_recent_turns < 0:
            raise ValueError(f"keep_recent_turns must be >= 0, got {self.keep_recent_turns}")
        if self.history_window_mode not in ("trim", "drop", "summarize"):
            raise ValueError(
                'history_window_mode must be "trim", "drop" or "summarize", '
                f"got {self.history_window_mode!r}"
            )
        if self.history_window_mode == "summarize" and self.summarizer is None:
            raise ValueError(
                'history_window_mode="summarize" requires a summarizer callable. '
                "It cannot be set from TOML — construct CompressorConfig in Python "
                "and pass summarizer=<your function>, or choose another mode."
            )
        if self.prefix_override is not None and self.prefix_override < 0:
            raise ValueError(f"prefix_override must be >= 0 or None, got {self.prefix_override}")
        if self.max_prompt_tokens is not None and self.max_prompt_tokens <= 0:
            raise ValueError(
                f"max_prompt_tokens must be > 0 or None, got {self.max_prompt_tokens}"
            )
        if self.budget_min_recent_turns < 1:
            raise ValueError(
                "budget_min_recent_turns must be >= 1 (dropping the final turn would "
                f"delete the question being asked), got {self.budget_min_recent_turns}"
            )
        if not (1 <= self.prefix_timestamp_bucket_minutes <= 60):
            raise ValueError(
                "prefix_timestamp_bucket_minutes must be between 1 and 60, "
                f"got {self.prefix_timestamp_bucket_minutes}"
            )

        # Resolve a preset name to its regex; a value that isn't a known
        # preset name is assumed to already be a raw regex.
        pattern = STAGE_DIRECTION_PRESETS.get(self.stage_direction_pattern, self.stage_direction_pattern)
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise ValueError(
                f"stage_direction_pattern is not a valid regex and not a known preset "
                f"({sorted(STAGE_DIRECTION_PRESETS)}): {self.stage_direction_pattern!r} ({e})"
            ) from e
        self.stage_direction_pattern = pattern
        self._compiled_stage_direction_pattern = compiled

    @classmethod
    def from_toml(cls, path: str | Path) -> CompressorConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> CompressorConfig:
        section = data.get("compressor", data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        # summarizer is a valid field name but not a valid *config-file*
        # key: TOML can only produce data, never a callable, so a value
        # here would sail through the name filter below and then fail at
        # call time deep inside compression. Reject it up front with an
        # explanation instead.
        if "summarizer" in section:
            raise ValueError(
                "summarizer cannot be set from a config file — it is a Python "
                "callable. Load this config, then assign it: "
                "config.summarizer = my_function"
            )
        kwargs = {k: v for k, v in section.items() if k in known}
        unknown = set(section) - known
        if unknown:
            raise ValueError(
                f"unknown compressor config key(s): {sorted(unknown)}. "
                f"Known keys: {sorted(known)}"
            )
        return cls(**kwargs)


@dataclass
class ProxyConfig:
    upstream_base_url: str = "https://api.deepseek.com/v1"
    upstream_api_key_env: str = "UPSTREAM_API_KEY"
    host: str = "127.0.0.1"
    port: int = 8791
    compressor: CompressorConfig = field(default_factory=CompressorConfig)

    # Off by default (empty string / unset env var = no access control, matching
    # prior zero-config behavior). Set this to the name of an env var holding a
    # shared secret to require every /v1/chat/completions caller to present it
    # as `Authorization: Bearer <secret>` — otherwise anyone who can reach the
    # proxy's host:port can make it spend your real upstream API key on their
    # behalf. Distinct from upstream_api_key_env: that one is the *real*
    # provider key this proxy sends upstream; this one is a separate secret
    # that only gates access to the proxy itself and is never forwarded.
    client_auth_token_env: str = ""

    @classmethod
    def from_toml(cls, path: str | Path) -> ProxyConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        proxy_section = data.get("proxy", {})
        compressor = CompressorConfig.from_dict(data)
        known = {"upstream_base_url", "upstream_api_key_env", "host", "port", "client_auth_token_env"}
        unknown = set(proxy_section) - known
        if unknown:
            raise ValueError(
                f"unknown proxy config key(s) in [{path}]: {sorted(unknown)}. "
                f"Known keys: {sorted(known)}"
            )
        kwargs = {k: v for k, v in proxy_section.items() if k in known}
        return cls(compressor=compressor, **kwargs)
