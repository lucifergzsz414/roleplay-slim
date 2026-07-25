# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning
follows [Semantic Versioning](https://semver.org/) — while the major
version is `0`, breaking changes may still land in a minor release (per
semver's own carve-out for `0.x`); patch releases are always safe to pull.

## [Unreleased]

### Added
- `CompressorConfig` now validates its fields on construction (`keep_recent_turns`,
  `history_window_mode`, `prefix_override`, `stage_direction_pattern`) and raises
  `ValueError` with a specific, actionable message instead of failing confusingly
  later during compression.
- `STAGE_DIRECTION_PRESETS`: named regex presets (`fullwidth_parens`, `halfwidth_parens`,
  `asterisk`, `square_bracket`) for `stage_direction_pattern`, so most apps don't need
  to write their own regex.
- `py.typed` marker — the package now advertises inline type hints to type checkers.
- `enable_prefix_normalize` / `prefix_timestamp_bucket_minutes` (off by default): an opt-in
  escape hatch for apps whose prefix embeds a live timestamp, which otherwise defeats the
  provider's cache on every request regardless of anything else this library does. Rounds
  ISO-8601 timestamps in the prefix down to a bucket boundary instead of leaving them exact
  — deliberately not a placeholder-style replacement (unlike Kompact's Cache Aligner), since
  a roleplay persona often genuinely needs approximate time-of-day information. Added after
  actually reading Kompact's `cache_aligner.py` source to verify how it differs (see below).

### Fixed
- `compress()` no longer crashes on OpenAI-style multimodal `content` (a list of
  `{"type": "text"|"image_url", ...}` parts instead of a plain string). Every
  text-manipulation strategy now only touches string content; list content passes
  through unmodified. Token estimation counts only the text parts.
- A system message that recurs across 2+ turns (e.g. a footer/format reminder) could
  be silently dropped entirely if every turn carrying a copy got pruned by
  `history_window`. `compress()` now snapshots recurring instructions before any
  pruning and re-attaches one copy if every copy is otherwise lost. Found via a live
  DeepSeek A/B comparison where the compressed request's reply visibly skipped a
  mandatory reply-format instruction the uncompressed request's reply followed.

### Testing
- Added `tests/test_properties.py`: Hypothesis-based property tests generating a wide
  range of message-array shapes (varying prefix length, turn count, recurring system
  messages, multimodal content) and checking invariants that must hold for any input.
- Added `tests/test_proxy.py`: proxy-layer tests using `httpx.MockTransport` in place
  of the real upstream call, so compression call-through, header passthrough, and
  streaming can be verified with zero network dependency and zero API cost.
- Added `tests/test_config.py` for the new validation/preset behavior.
- Added a GitHub Actions CI workflow running the full suite on Python 3.10/3.11/3.12.

## [0.1.0] — 2026-07-25

Initial release.

- Core library (`roleplay_slim.compress`): segments an OpenAI-format `messages` array
  into a cache-stable prefix (left untouched) and a dynamic region, then applies
  `whitespace_normalize`, `dedupe_verbatim_tail`, `history_window`, and (opt-in)
  `strip_stage_directions`.
- OpenAI-compatible proxy (`roleplay-slim-proxy`) with `/v1/chat/completions`,
  `/stats`, `/healthz`.
- Validated against two independent real third-party app message structures
  (a production QQ roleplay bot, and a separate Python-based desktop companion app)
  in addition to hand-written fixtures.
