"""Configuration model for roleplay-slim.

A CompressorConfig controls how much of the "dynamic" region (everything
after the leading system-message prefix) gets compressed, and how. Every
field has a safe default so ``CompressorConfig()`` alone is usable.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


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
    # descriptions, e.g. r"（[^）]*）" for full-width parens (used by the
    # xiaomu bot this project was built against) or r"\*[^*]*\*" for
    # markdown-style *asterisks*. Only consulted when
    # enable_strip_stage_directions is True.
    stage_direction_pattern: str = r"（[^）]*）"

    # Override the auto-detected prefix length (see segmenter.detect_prefix).
    # None means "auto-detect: all leading system messages before the first
    # user/assistant message". Set an explicit int only when the app's
    # cache-stable prefix doesn't match that heuristic.
    prefix_override: int | None = None

    @classmethod
    def from_toml(cls, path: str | Path) -> "CompressorConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("compressor", data)
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
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
