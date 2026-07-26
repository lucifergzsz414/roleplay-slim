# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning
follows [Semantic Versioning](https://semver.org/) — while the major
version is `0`, breaking changes may still land in a minor release (per
semver's own carve-out for `0.x`); patch releases are always safe to pull.

## [Unreleased]

### Added
- `client_auth_token_env` (off by default): an optional shared-secret check that gates
  access to the proxy itself, separate from the real upstream provider key. Without it,
  anyone who can reach the proxy's `host:port` can spend your configured upstream API key
  on their behalf — fine for `127.0.0.1`-only use, not fine once the proxy is reachable
  from anywhere else. Flagged in an external code review; verified against a real running
  proxy that missing/wrong tokens get a proxy-level 401 and the configured token never
  leaks to the upstream request.
- `[all]` extra (`pip install "roleplay-slim[all]"`) — a single install command combining
  `[proxy,tokens]` so new users don't need to know the individual extra names. Verified via
  `pip download` that the full dependency closure is under 5 MB, versus 11+ MB for Kompact
  (which bundles a full OpenTelemetry stack) and considerably more for headroom-ai's `[proxy]`
  extra (onnxruntime, transformers, magika, among others).
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
- The proxy now prints a one-line, human-readable summary for every request
  (`[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)`) to whatever
  terminal it's running in, instead of requiring a `/stats` poll to see anything is
  happening. Scoped to roleplay-slim's own logger (not the root logger) so it doesn't
  leak its prefix onto httpx's or uvicorn's own log lines.
- `QUICKSTART.md` / `QUICKSTART_CN.md`: a from-zero setup guide (English + Chinese)
  assuming no prior experience with Python packaging, environment variables, or this
  project's own concepts — separate from the README, which assumes more context.

- The proxy now logs a clear warning at startup (not just a confusing 401 at the first
  request) if no upstream API key is configured and no fallback env var is set.

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
- The proxy reused a fresh `httpx.AsyncClient` per request instead of one shared client for
  the app's lifetime, discarding connection pooling on every single call. Fixed by moving
  client creation into the app's `lifespan` (FastAPI's `on_event("shutdown")` is itself
  deprecated, so this also modernized to the `lifespan` context-manager form).
- `ProxyConfig.from_toml()` opened and parsed the same config file twice — once directly,
  once via `CompressorConfig.from_toml(path)` internally. Now parsed once and shared.
- `strip_stage_directions()` recompiled `stage_direction_pattern` on every call instead of
  once; `CompressorConfig.__post_init__` now compiles it once and caches the result.
- Upstream connection failures (timeout, connection refused) previously crashed with an
  unhandled exception instead of a clean error response — both the regular and streaming
  request paths now return a `502` with an error body on upstream failure. Streaming
  requests also now propagate the real upstream status code and content-type instead of
  always responding `200` regardless of what the upstream actually returned, and close the
  upstream connection cleanly if the stream is interrupted partway through.
- All four items above came out of an external code review (GLM + qwen3.7-max, run with a
  strict format requiring specific file:line findings rather than a general summary) —
  verified each against the actual source before fixing, ruled out one flagged issue as a
  false positive from the reviewer prompt's own encoding mishap (not a real bug in the
  shipped code), and end-to-end tested the connection-reuse and auth fixes against a real
  running proxy talking to the real DeepSeek API rather than trusting unit tests alone.

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
