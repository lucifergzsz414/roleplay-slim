"""Configuration model for roleplay-slim.

A CompressorConfig controls how much of the "dynamic" region (everything
after the leading system-message prefix) gets compressed, and how. Every
field has a safe default so ``CompressorConfig()`` alone is usable.
"""
from __future__ import annotations

import re
import sys
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
    "fullwidth_parens": r"（[^）]*）",  # （挥手）— the xiaomu bot's convention
    "halfwidth_parens": r"\([^)]*\)",  # (waves)
    "asterisk": r"\*[^*]*\*",  # *waves*
    "square_bracket": r"\[[^\]]*\]",  # [waves] — careful: matches format-tag
    # brackets like [信任:+0] too if you use those; prefer a distinct
    # convention for stage directions if your app also uses bracket tags.
}


@dataclass
class CompressorConfig:
    # How many of the most recent user/assistant turn-pairs are left
    # completely untouched. Everything older than this window is a
    # candidate for history_window / strip_stage_directions.
    keep_recent_turns: int = 3

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
    # for apps, like qqbot's own _retrieve_memory, that already inject a
    # separate persistent-memory summary elsewhere); "trim" keeps a short
    # extractive stub (first + last sentence) instead of dropping entirely.
    history_window_mode: str = "trim"  # "trim" | "drop"

    # Regex describing how this app wraps stage directions / action
    # descriptions. Either a raw regex string (e.g. r"（[^）]*）") or the
    # name of a STAGE_DIRECTION_PRESETS entry (e.g. "fullwidth_parens"),
    # resolved in __post_init__. Only consulted when
    # enable_strip_stage_directions is True.
    stage_direction_pattern: str = "fullwidth_parens"

    # Override the auto-detected prefix length (see segmenter.detect_prefix).
    # None means "auto-detect: all leading system messages before the first
    # user/assistant message". Set an explicit int only when the app's
    # cache-stable prefix doesn't match that heuristic.
    prefix_override: int | None = None

    def __post_init__(self) -> None:
        if self.keep_recent_turns < 0:
            raise ValueError(f"keep_recent_turns must be >= 0, got {self.keep_recent_turns}")
        if self.history_window_mode not in ("trim", "drop"):
            raise ValueError(
                f'history_window_mode must be "trim" or "drop", got {self.history_window_mode!r}'
            )
        if self.prefix_override is not None and self.prefix_override < 0:
            raise ValueError(f"prefix_override must be >= 0 or None, got {self.prefix_override}")

        # Resolve a preset name to its regex; a value that isn't a known
        # preset name is assumed to already be a raw regex.
        pattern = STAGE_DIRECTION_PRESETS.get(self.stage_direction_pattern, self.stage_direction_pattern)
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(
                f"stage_direction_pattern is not a valid regex and not a known preset "
                f"({sorted(STAGE_DIRECTION_PRESETS)}): {self.stage_direction_pattern!r} ({e})"
            ) from e
        self.stage_direction_pattern = pattern

    @classmethod
    def from_toml(cls, path: str | Path) -> "CompressorConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("compressor", data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in section.items() if k in known}
        return cls(**kwargs)


@dataclass
class ProxyConfig:
    upstream_base_url: str = "https://api.deepseek.com/v1"
    upstream_api_key_env: str = "UPSTREAM_API_KEY"
    host: str = "127.0.0.1"
    port: int = 8791
    compressor: CompressorConfig = field(default_factory=CompressorConfig)

    @classmethod
    def from_toml(cls, path: str | Path) -> "ProxyConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        proxy_section = data.get("proxy", {})
        compressor = CompressorConfig.from_toml(path)
        known = {"upstream_base_url", "upstream_api_key_env", "host", "port"}
        kwargs = {k: v for k, v in proxy_section.items() if k in known}
        return cls(compressor=compressor, **kwargs)
