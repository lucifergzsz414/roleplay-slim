# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning
follows [Semantic Versioning](https://semver.org/) — while the major
version is `0`, breaking changes may still land in a minor release (per
semver's own carve-out for `0.x`); patch releases are always safe to pull.

## [Unreleased]

### Added

- Two new `examples/`: `telegram_bot.py` (library-mode skeleton, same
  pattern as the existing Discord example) and
  `openwebui_style_adapter.py` (documents the zero-code-change proxy-mode
  integration for any generic OpenAI-compatible chat UI — OpenWebUI,
  LibreChat, and similar — plus a library-mode dry-run for anyone building
  their own from scratch). Both verified to actually run end-to-end, not
  just pass lint.
- Real-shape validation for the prefix optimizer
  (`tests/test_optimizer_real_shape.py`), closing the item the optimizer's
  own design doc left open. No real request content — a synthetic corpus
  whose *structural* parameters (prefix message count, turn-count spread, a
  recurring footer) match traffic actually observed on a production
  deployment. Confirms `analyze()` still recommends exactly the true
  prefix, and projects a cache-hit ceiling in the same order of magnitude
  as the real, previously-measured figure, under a messier corpus than the
  clean single-shape one in `test_optimizer.py`.

## [0.4.1] — 2026-08-20

### Fixed

- `StatsStore.record_usage()` back-filled "the latest row" rather than the
  specific request it belonged to. Under concurrent requests, a second
  request's row could be inserted between the first request's row and its
  response coming back, silently attributing usage to the wrong request —
  found via two real near-simultaneous production requests, one ending up
  with no upstream data recorded and the other carrying someone else's.
  `record()` now returns the inserted row's `id`; `record_usage()` requires
  it and updates that exact row. `StatsStore` is an internal implementation
  detail (not exported from the package), so this is a patch, not a
  behavior change any caller outside the proxy can observe.

## [0.4.0] — 2026-08-16

### Added

- **`optimize` CLI command + `roleplay_slim.optimizer`**: cross-request prefix
  optimization. Given a batch of captured requests, reports which leading
  message positions are stable across requests (appearing in ≥90% of samples
  AND byte-identical when present) and the longest leading stable run as the
  recommended prefix, with a cache-hit-ceiling estimate before/after
  hoisting. The tool never touches your config.
- **`/stats` persistence**: the proxy's `/stats` now answers from a SQLite
  store instead of an in-memory object, so the numbers survive restarts.
  Configurable via `[stats] persist` (default `true`) and `db_path`;
  `persist = false` falls back to a pure in-memory accumulator with identical
  output.
- **Multi-client-token auth**: `client_auth_tokens_extra` — a comma-separated
  list of additional client credentials the proxy accepts alongside
  `client_auth_token_env`, so more than one caller can authenticate against a
  single instance. All credentials compared with `secrets.compare_digest`;
  the gate stays fail-closed and never forwards proxy credentials upstream.
- **Synthetic fidelity benchmark** (`benchmark/corpus_gen.py` +
  `benchmark/run_fidelity.py`): seeded generator planting verifiable facts in
  the dynamic region, measuring token savings AND fact retention — the
  publishable evidence that compression doesn't lose persona memory.
- CI: ruff lint (explicit rule set) + a packaging gate
  (`scripts/check_sdist.py`) that fails if any file ships outside the sdist
  allowlist — it caught two real leaks on first run.
- Design docs for the roadmap (`docs/designs/`).

### Fixed

- Bare `mypy` invocation broke on mypy 2.x (which no longer honours the
  `packages` config key as a default target); CI now passes `mypy src`.
- The stats store degrades to in-memory instead of crashing the proxy when
  its SQLite file can't be opened (e.g. a relative default `db_path` under
  a systemd service whose CWD isn't writable) — telemetry must never take
  down a live request path. A warning is logged so the loss of persistence
  is visible.
- Tests importing the `benchmark` package failed under the `pytest` console
  script (no CWD on `sys.path`); a root `conftest.py` fixes it.
- `build.py` now fails with an actionable message when the private pet
  `使用说明*.txt` manuals are absent, instead of a raw `FileNotFoundError`.

### Removed

- The desktop-pet `使用说明*.txt` manuals were removed from the public repo
  (they are private distribution content, not part of the library).

## [0.3.2] — 2026-08-13

No functional change. 0.3.1's source distribution shipped files that were never
meant to be in it; this release is the same code, packaged correctly. PyPI does
not allow replacing the files of a published release, so the fix has to arrive
as a new version — pull this one instead of 0.3.1.

### Fixed

- The sdist shipped whatever happened to be sitting in the working tree. Only the wheel
  had an explicit file list, so hatchling built the sdist by sweeping in everything the
  `.gitignore` didn't happen to exclude — which in 0.3.1 meant the desktop-pet GUI
  installer (a side utility, not part of the package) and the whole `.hypothesis` test
  cache. The sdist now has its own allowlist, so a stray file can't ship by accident.
  No published release ever contained credentials; this is a packaging-hygiene fix.

### Added

- README: the prefix is never compressed by design, so its share of the prompt is a hard
  ceiling on what compression can reach — worth checking with `preview` before judging
  the savings you get. Also documented that `cl100k_base` overcounts CJK text relative to
  what providers with their own tokenizers bill, which inflates the absolute figures in
  `/stats` (the ratio survives; the absolute numbers don't).

## [0.3.1] — 2026-08-12

### Fixed

- `roleplay_slim.__version__` reported `0.1.0` on every release since 0.1.0 — it was a
  hand-written literal that nobody bumped, so it silently disagreed with the version
  recorded in the package metadata. Anything that introspected the installed version
  (a deployment check, a bug report, a `/stats` banner) got a two-releases-stale answer.
  It now tracks the installed distribution metadata via `importlib.metadata`, so there is
  no second place to forget. A test asserts the two agree, which is the part that keeps
  this fixed.

## [0.3.0] — 2026-08-11

### Added

- `max_prompt_tokens`: an actual ceiling on the compressed prompt. Every setting until
  now was structural — "keep the last 6 turns" says nothing about how large those turns
  are, so the output had no upper bound at all. The gap is not theoretical: the
  extractive trim in `history_window` splits on sentence-ending punctuation, and short
  chat lines often have none (`"ok"`, `"在吗"`), so it returns them unchanged. Measured
  on 40 turns of that traffic: 1.1% saved without a budget, 90.0% with one. Whole turns
  are dropped rather than individual messages, keeping `tool_calls` → `tool` chains
  intact. The prefix, the most recent `budget_min_recent_turns` turns, and recurring
  system messages are never sacrificed to meet it; an unmeetable budget is logged and
  left unmet rather than honoured by breaking the prompt, and never raises. Off by
  default — a config that doesn't mention it is byte-for-byte unchanged.
- `history_window_mode = "summarize"` plus a `summarizer` callable: hands the aged-out
  history to your own condensing function and replaces the whole block with the one
  string it returns. The library still takes no ML dependency and still doesn't rewrite
  anyone's dialogue itself — apps in this space usually already have a memory layer whose
  output beats a regex. On 50 turns of punctuation-free chat: `trim` 2.7%, `drop` 92.6%,
  `summarize` 92.2% — but `drop` gets there by discarding the history, while `summarize`
  keeps a condensed memory of it. The callback may fail: raising or returning a
  non-string is logged and falls back to `trim`, an empty return drops the block, and it
  isn't called at all when nothing has aged out.
- `/stats` now reports the provider's own token accounting alongside the local estimates,
  under an `upstream` key — including `prompt_cache_hit_tokens` on providers that expose
  it. Preserving the upstream prefix cache is this project's central claim and it
  previously had no measurement behind it. The block is `null` before anything is
  measured rather than a row of zeroes, and the cache fields stay `null` on providers
  that don't report a breakdown instead of collapsing to a misleading 0% hit rate.
  Non-streaming responses only — a streamed response carries usage only when the caller
  sets `stream_options.include_usage`.
- The proxy forwards any `/v1/*` endpoint other than chat completions (`/v1/models`,
  `/v1/embeddings`, …) verbatim and uncompressed. SillyTavern, OpenWebUI and similar
  clients fetch `GET /v1/models` on connect to populate a model picker, and the 404 they
  got instead read as a broken server before a single message could be sent. The
  catch-all goes through the same `client_auth_token_env` gate as the chat endpoint —
  it spends the same upstream key.
- `roleplay-slim preview`: a new CLI that shows what compression would do to a captured
  conversation, with no network call and nothing written. Reads a messages array or a
  full request body, from a file or stdin. `--json` emits the compressed messages;
  `--quiet` gives the summary alone. It deliberately does not claim a one-to-one mapping
  between original and compressed messages — none reliably exists — and reports instead
  whether content survives verbatim, plus the resulting prompt itself.

### Fixed

- The proxy no longer forwards a stale `content-encoding` header on a response body that
  httpx has already decompressed. Clients without brotli support (Unity's
  `UnityWebRequest` among them) failed with "Unrecognized content-encoding" even though
  the bytes they received were plain text.

### Notes

- `roleplay-slim-proxy` is unchanged and existing service files keep working; the new
  `roleplay-slim` entry point is additive.
- Test count: 88 → 167.

## [0.2.0] — 2026-07-26

### Added

- `client_auth_token_env` (off by default): an optional shared-secret check that gates
  access to the proxy itself, separate from the real upstream provider key. Without it,
  anyone who can reach the proxy's `host:port` can spend your configured upstream API key
  on their behalf — fine for `127.0.0.1`-only use, not fine once the proxy is reachable
  from anywhere else. Token comparison uses `secrets.compare_digest` (timing-safe).
- `[all]` extra (`pip install "roleplay-slim[all]"`) — a single install command combining
  `[proxy,tokens]` so new users don't need to know the individual extra names.
- `STAGE_DIRECTION_PRESETS`: named regex presets (`fullwidth_parens`, `halfwidth_parens`,
  `asterisk`, `square_bracket`) for `stage_direction_pattern`, so most apps don't need
  to write their own regex.
- `py.typed` marker — the package now advertises inline type hints to type checkers.
- `enable_prefix_normalize` / `prefix_timestamp_bucket_minutes` (off by default): an opt-in
  escape hatch for apps whose prefix embeds a live timestamp, which otherwise defeats the
  provider's cache on every request. Rounds ISO-8601 timestamps in the prefix down to a
  bucket boundary instead of leaving them exact.
- The proxy now prints a one-line, human-readable summary for every request
  (`[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)`).
- `QUICKSTART.md` / `QUICKSTART_CN.md`: a from-zero setup guide (English + Chinese).
- The proxy now logs a clear warning at startup if no upstream API key is configured.
- **Hop-by-hop header filtering** (RFC 2616 §13.5.1): proxy now strips `Connection`,
  `Transfer-Encoding`, `Keep-Alive`, `Upgrade`, `Proxy-Authenticate`,
  `Proxy-Authorization`, `TE`, and `Trailers` from both request and response forwarding,
  instead of only stripping two headers.
- **Raw response pass-through**: non-JSON upstream responses (e.g. CDN/gateway HTML error
  pages) are now passed through via `Response(content=...)` instead of crashing the proxy
  with a `JSONDecodeError`.
- **Request body validation**: the proxy now returns a clear `400` for common caller
  mistakes (body isn't valid JSON, body isn't a dict, `messages` isn't an array, message
  elements aren't dicts) rather than failing confusingly later.
- **Request header and query-string forwarding**: the proxy now forwards client-provided
  HTTP headers and query parameters to the upstream provider, so custom headers (e.g.
  `X-Custom-Provider-Param`) and query flags are no longer silently dropped.
- `CompressorConfig` now validates all fields on construction (`keep_recent_turns`,
  `history_window_mode`, `prefix_override`, `stage_direction_pattern`) and raises
  `ValueError` with a specific, actionable message.
- **Strict config validation**: `CompressorConfig.from_dict` and `ProxyConfig.from_toml`
  now raise `ValueError` on unknown keys, so a typo like `client_auth_token_en` doesn't
  silently disable proxy access control without any warning.

### Changed

- **Prefix detection now includes `developer` role**: the auto-detected cache-stable
  prefix treats leading `developer` messages the same as `system` (the
  OpenAI-recommended replacement for `system` as of 2025), so they're left
  byte-for-byte untouched.
- **Asterisk preset regex improved**: changed from `r"\*[^*]*\*"` to
  `r"(?<!\*)\*[^*]+\*(?!\*)"` so markdown `**bold**` and `***bold-italic***` are
  no longer matched/stripped. Note: single-asterisk `*italic*` emphasis *will* still
  match — apps using markdown in dialogue should prefer a distinct convention like
  `fullwidth_parens`, `halfwidth_parens`, or `square_bracket`.

### Fixed

- `compress()` no longer crashes on OpenAI-style multimodal `content` (a list of
  `{"type": "text"|"image_url", ...}` parts instead of a plain string). Every
  text-manipulation strategy now only touches string content; list content passes
  through unmodified.
- A system message that recurs across 2+ turns (e.g. a footer/format reminder) could
  be silently dropped entirely if every turn carrying a copy got pruned by
  `history_window`. `compress()` now snapshots recurring instructions before any
  pruning and re-attaches one copy if every copy is otherwise lost. Found via a live
  DeepSeek A/B comparison.
- **Recurring system messages now preserve extra fields** (`name`, `cache_control`, etc.)
  when re-attached after compression — they were previously flattened to bare
  `{role, content}` skeletons.
- **`_extractive_trim` length guard**: very short messages where the "……" connector
  plus two boundary sentences already exceeds the original length are now returned
  unchanged instead of being "compressed" into a longer string.
- The proxy reused a fresh `httpx.AsyncClient` per request instead of one shared client for
  the app's lifetime, discarding connection pooling on every single call. Fixed by moving
  client creation into the app's `lifespan`.
- `ProxyConfig.from_toml()` opened and parsed the same config file twice — once directly,
  once via `CompressorConfig.from_toml(path)` internally. Now parsed once and shared.
- `strip_stage_directions()` recompiled `stage_direction_pattern` on every call instead of
  once; `CompressorConfig.__post_init__` now compiles it once and caches the result.
- Upstream connection failures (timeout, connection refused) previously crashed with an
  unhandled exception instead of a clean error response — both the regular and streaming
  request paths now return a `502` with an error body on upstream failure.
- `history_window` trim mode now preserves `tool`-role messages unmodified (they're
  structured data, often JSON — sentence-boundary trimming would corrupt them) and keeps
  tool-call chains (assistant.tool_calls → tool → assistant) atomically intact.

### Testing

- Added `tests/test_properties.py`: Hypothesis-based property tests generating a wide
  range of message-array shapes and checking invariants that must hold for any input.
- Added `tests/test_proxy.py`: proxy-layer tests using `httpx.MockTransport` (10 new
  tests covering validation, pass-through, header forwarding, hop-by-hop filtering).
- Added `tests/test_config.py`: config validation/preset tests (4 new tests for strict
  validation).
- 2 new segmenter tests (developer prefix detection), 7 new strategy tests (tool
  preservation, developer dedup, extractive trim guard, asterisk preset), 2 new E2E
  tests (developer prefix, tool-call chain survival, recurring footer field preservation).
- 86 tests total, all passing; mypy reports zero issues.
- Added a GitHub Actions CI workflow running the full suite on Python 3.10/3.11/3.12.

### Security

- Token comparison uses `secrets.compare_digest` (constant-time) instead of string
  equality for `client_auth_token_env` shared-secret validation.
- Hop-by-hop headers are now comprehensively stripped per RFC 2616 §13.5.1 (was only
  stripping `authorization` from responses — a subset of the required set).
- Request body is validated early (before any upstream call) with clear 400 responses
  for malformed input.
- Strict config validation prevents typos in security-critical fields from silently
  disabling access control.

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
