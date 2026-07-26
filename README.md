# roleplay-slim

A lightweight context optimization layer for persistent AI characters —
a library and an OpenAI Chat Completions-compatible proxy that knows the difference between a
**cache-stable persona prefix** and the **dialogue history you pay for on
every request**.

New to this and just want it running? See
[QUICKSTART.md](QUICKSTART.md) ([中文版](QUICKSTART_CN.md)) — no prior
experience assumed.

## Why this exists

There's already a handful of LLM context-compression proxies on GitHub —
[headroom](https://github.com/chopratejas/headroom),
[kompact](https://github.com/npow/kompact),
[KrunchWrapper](https://github.com/naemlucifer/KrunchWrapper), and others.
They're good at what they target: JSON blobs, tool output, logs, code.
headroom's own numbers make the point — ~20% savings on coding-agent
traffic, 60-95% on JSON. That's because those tools only see raw text and
compress it generically.

None of them are built for **prose dialogue** — the kind a roleplay
companion bot, character.ai-style app, or long-running chat memory system
actually sends. Squeezing that content the same way a generic compressor
squeezes JSON risks losing the tone, phrasing, and emotional nuance that
*is* the product. roleplay-slim is built specifically for that content
instead.

## The core idea

Most chat apps send a message array shaped like:

```
[system]  <fixed persona / instructions — identical on every request>
[system]  <fixed shared config — identical on every request>
[user]    <turn 1>
[assistant] <turn 1 reply>
[system]  <per-turn footer / reminder — repeated verbatim every request>
[user]    <turn 2>
...
```

The leading system messages are usually **byte-identical across requests**
— and providers like DeepSeek reward that with automatic prefix-based
prompt caching. Compress that block differently on every call and you
silently defeat the provider's own caching for no benefit. The dialogue
history and footers after it, though, are paid for in full, every single
time.

roleplay-slim's default heuristic auto-detects that split — every leading
`system` message before the first `user`/`assistant` message is left
**byte-for-byte untouched**; compression only ever runs on what comes
after. No per-app config required to get that much for free.

This is a real guarantee, not a best-effort one: `compress()` never runs a
single transform on the prefix region by default. That's a different
approach from, say, [Kompact](https://github.com/npow/kompact)'s "Cache
Aligner" (verified by reading its source, not just its README) — Kompact
lets earlier layers in its pipeline compress the system prompt like
anything else, then tries to restore cache-ability afterward by finding
volatile substrings (UUIDs, timestamps) and replacing them with opaque
placeholders. roleplay-slim instead identifies the prefix *before* any
strategy runs and simply never touches it — no reconstruction needed
because nothing was changed in the first place.

## What it compresses (v0.2, all rule-based — no ML model, no lossy
semantic scoring)

| Strategy | What it does | Default |
|---|---|---|
| `whitespace_normalize` | Collapses redundant blank lines/whitespace | on |
| `dedupe_verbatim_tail` | If the exact same footer/reminder text repeats across turns, keeps only the last copy | on |
| `history_window` | Keeps the most recent N turns verbatim; older turns get dropped or trimmed to a first+last-sentence stub | on |
| `strip_stage_directions` | Removes parenthetical/action-description text (`（…）`, `*…*`, whatever your app uses) from *older* turns only, keeping actual dialogue intact — the differentiator | off (format-sensitive, opt-in) |

**Explicitly out of scope for v0.2** (see the plan doc if you're
contributing): no LLMLingua-style ML-based semantic compression, no
multi-provider wire-format translation (OpenAI format only — covers
DeepSeek and most others), no cross-request semantic cache/vector store,
no GUI.

### `history_window`'s trim mode assumes you have (or don't need) memory elsewhere

`trim` keeps a naive first+last-sentence stub of older turns — it has no
concept of which sentence matters. A promise or commitment buried in the
*middle* of an older message ("下次给你带一束花" in the middle of a longer
message about the weather) can end up in the dropped middle section.

This is a deliberate scope boundary, not an oversight: roleplay-slim is a
token compressor, not a memory system, and it has no way to know which
sentence in your app's specific domain is the important one without either
an LLM call (which the v0.2 rule-based boundary above rules out) or a
fragile keyword heuristic (high false-negative rate, especially outside
English). If your app doesn't have its own long-term memory/fact-extraction
layer sitting *before* compression runs, either:

- switch to `history_window_mode="drop"` and rely on `keep_recent_turns`
  alone (no partial-content risk, just a harder cutoff), or
- extract anything that must never be lost (promises, key facts, relationship
  state) into your own persistent store *before* calling `compress()` —
  `history_window_mode="drop"` is exactly the mode meant to pair with an app
  that already does this (see `examples/qqbot_style_config.toml`'s comment).

### If your own prefix isn't actually static

The byte-for-byte guarantee above only helps if your prefix genuinely is
identical across requests. If your app embeds something that changes
every call — most commonly a live timestamp baked into the persona/shared
config block — the prefix breaks the provider's cache on its own, and
roleplay-slim's default behavior (never touch it) can't fix that for you.

`enable_prefix_normalize` (off by default) is a narrow, opt-in escape
hatch for exactly that case: it rounds ISO-8601 timestamps found in the
prefix down to the nearest `prefix_timestamp_bucket_minutes` boundary
(default 5) instead of leaving them exact to the second. Unlike stripping
the timestamp into an opaque placeholder, this keeps genuinely useful
time-of-day information — a roleplay persona often needs to know roughly
what time it is — while still making requests within the same bucket
window byte-identical.

```python
config = CompressorConfig(
    enable_prefix_normalize=True,
    prefix_timestamp_bucket_minutes=5,
)
```

## Multimodal content

`content` can be a plain string or an OpenAI-style list of `{"type":
"text"|"image_url", ...}` parts (vision requests). Every text-manipulating
strategy only ever touches string content — a list is passed through
byte-for-byte unmodified rather than guessed at. Token estimates only
count the text parts of a multimodal message; image cost isn't modeled.

## Testing

Hand-picked fixtures alone missed a real bug (a recurring instruction could
be silently pruned depending on which turns happened to carry it — see the
commit history). In addition to the fixture-based tests,
`tests/test_properties.py` uses [Hypothesis](https://hypothesis.readthedocs.io/)
to generate a wide range of message-array shapes (varying prefix length,
turn count, and which system messages repeat across turns) and checks
invariants that must hold for *any* input — the prefix survives unchanged,
a recurring instruction never vanishes entirely, output stays well-formed.
Run `pytest` to execute both.

## Install

```bash
pip install roleplay-slim              # library only
pip install "roleplay-slim[proxy]"     # + the HTTP proxy
pip install "roleplay-slim[all]"       # proxy + accurate tiktoken-based stats, one shot
```

`[all]` is just `[proxy,tokens]` — it exists so you don't have to remember the
extra names to get everything working; the full dependency closure is under
5 MB (verified — nowhere near the ML-heavy `[proxy]` extras some other tools
in this space pull in).

## Use as a library

```python
from roleplay_slim import compress, CompressorConfig

config = CompressorConfig(
    keep_recent_turns=3,
    enable_strip_stage_directions=True,
    stage_direction_pattern="fullwidth_parens",  # or "asterisk", "halfwidth_parens",
                                                  # "square_bracket", or your own raw regex
)
compressed_messages = compress(messages, config)
```

## Use as a proxy (zero code changes — just swap the base URL)

```bash
export UPSTREAM_API_KEY=sk-...
roleplay-slim-proxy --config examples/qqbot_style_config.toml
```

Then point your app at `http://127.0.0.1:8791/v1` instead of the real
provider — everything else (auth header passthrough, streaming) works the
same.

Every request prints a one-line summary to the terminal you ran it from,
so you can see compression working without needing to poll `/stats`:

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

`GET /stats` returns the same numbers as running totals, in JSON, if you
want to pull them into your own monitoring instead.

See `examples/qqbot_style_config.toml` for a config modeled on a real
production roleplay bot's message structure.

### Securing the proxy itself

By default anyone who can reach the proxy's `host:port` can use it — and
since it holds your real upstream API key, that means they can spend your
money. Fine for `127.0.0.1`-only local use; not fine if you ever bind it to
`0.0.0.0` or put it behind a shared server. Set `client_auth_token_env` to
the name of an environment variable holding a shared secret to require every
caller to present it:

```toml
[proxy]
client_auth_token_env = "PROXY_ACCESS_TOKEN"
```

```bash
export PROXY_ACCESS_TOKEN=some-long-random-string
```

Callers then need `Authorization: Bearer some-long-random-string` — this is
checked against the proxy itself, separately from (and never forwarded to)
the real upstream provider, whose key stays configured via
`upstream_api_key_env` as before.

Errors and non-2xx responses from the real upstream provider (rate limits,
auth failures, timeouts) are passed through with their real status code and
body — both for regular and streaming requests — rather than silently
turning into an unhandled exception or a misleading 200.

## API coverage

| | |
|---|---|
| ✅ `POST /v1/chat/completions` | Full support — both regular and streaming |
| ✅ `system` / `user` / `assistant` roles | Full support |
| ✅ Streaming (SSE) | Transparent pass-through |
| ✅ OpenAI-compatible client libraries | Drop-in `base_url` swap |
| ⚠️ `developer` role | Supported in compression, not yet tested against every provider's wire format |
| ⚠️ Multimodal `content` (list of parts) | Passes through unmodified; token estimation counts text parts only |
| ⚠️ Tool calls (`tool_calls` → `tool` → `assistant`) | Chains are preserved atomically, but the proxy itself doesn't execute tools |
| ❌ `POST /v1/responses` | Not supported (Chat Completions only) |
| ❌ Function execution | The proxy compresses messages; it doesn't run your tools |

## Status

v0.2 — built and dogfooded against one production roleplay bot's traffic.
Contributions welcome, especially additional `stage_direction_pattern`
presets for other apps' conventions.

## License

MIT
