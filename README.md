# roleplay-slim

> Keep AI characters consistent through long conversations —
> compress what you pay for, protect what makes them themselves.

A lightweight context optimization layer for persistent AI characters —
a library and an OpenAI Chat Completions-compatible proxy that knows the difference between a
**cache-stable persona prefix** and the **dialogue history you pay for on
every request**.

[![CI](https://github.com/lucifergzsz414/roleplay-slim/actions/workflows/ci.yml/badge.svg)](https://github.com/lucifergzsz414/roleplay-slim/actions)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[中文版](README_CN.md) · New to this and just want it running? See
[QUICKSTART.md](QUICKSTART.md) ([中文版](QUICKSTART_CN.md)) — no prior
experience assumed.

> **500 turns later, the character still remembers who they are.**
> 37,176 → 19,545 tokens (47.4% less). Persona prefix never touched.
> [See the proof](#benchmarks) · [Try it in 30s](#30-second-tryout)

## The problem

```mermaid
flowchart LR
    subgraph WITHOUT["Without compression"]
        direction TB
        W1["100+ turns of chat"] --> W2["Context explosion"]
        W2 --> W3["Token cost rising"]
        W2 --> W4["Personality drift"]
    end
    subgraph WITH["With roleplay-slim"]
        direction TB
        S1["Persona — cached forever"] --> S3["Cache hits every request"]
        S2["History — compressed"] --> S4["~47% fewer tokens"]
        S3 --> S5["Character stays the same"]
        S4 --> S5
    end
    WITHOUT -.->|"same conversation"| WITH
```

## What it does

| | |
|---|---|
| **Library** | `compress(messages)` — import it in your own Python app |
| **Proxy** | Drop-in `base_url` swap, zero code changes — runs on `127.0.0.1:8791` |
| **Cache-aware** | Auto-detects the cache-stable prefix and leaves it byte-for-byte untouched |
| **Dialogue-native** | Built for prose, not JSON — stage-direction stripping, recurring-footer dedup, per-turn trimming |

### How it fits into your stack

```mermaid
flowchart TD
    subgraph RS["roleplay-slim proxy :8791"]
        direction LR
        P["Persona prefix — untouched, cache-safe"]
        D["Dialogue history — compressed"]
    end
    A[Your App] -->|"POST /v1/chat/completions"| RS
    RS -->|"compressed messages"| C[LLM Provider]
    C -->|"response"| RS
    RS -->|"response (passthru)"| A
```

---

## Why this exists

There's already a handful of LLM context-compression proxies on GitHub —
[headroom](https://github.com/headroomlabs-ai/headroom),
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

---

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

---

## Why not just use a generic compressor?

Most compression tools treat every message the same way — they see a block of
text and shrink it. That works for JSON or logs, but for roleplay dialogue it
creates two problems:

| | Generic compressor | roleplay-slim |
|---|---|---|
| **Persona prefix** | Compressed along with everything else — breaks the provider's prompt cache, and risks losing tone and phrasing | Left byte-for-byte untouched — hits the cache, character stays in character |
| **Dialogue history** | Compressed uniformly — can't tell the difference between a stage direction, a repeated footer, and actual dialogue | Compresses intelligently — strips stage directions from old turns, dedupes footers, trims stale turns, keeps recent dialogue verbatim |

```
Generic:  [persona] [history] → [compressed blob] → LLM
               ✗ cache broken    ✗ tone at risk

Slim:     [persona] → [untouched] ───────────────→ LLM
          [history] → [compressed] ───────────────→ LLM
               ✓ cache hits       ✓ dialogue preserved
```

roleplay-slim's compression strategies only ever run on the dialogue portion —
the persona prefix is structurally guaranteed to survive unchanged. That's not
a "best effort, hope it works" claim; `compress()` never runs a single
transform on the prefix region by default.

---

## What it compresses (v0.3 — rule-based by default, no ML model, no lossy
semantic scoring)

| Strategy | What it does | Default |
|---|---|---|
| `whitespace_normalize` | Collapses redundant blank lines/whitespace | on |
| `dedupe_verbatim_tail` | If the exact same footer/reminder text repeats across turns, keeps only the last copy | on |
| `history_window` | Keeps the most recent N turns verbatim; older turns get dropped, trimmed to a first+last-sentence stub, or handed to your own summarizer | on |
| `strip_stage_directions` | Removes parenthetical/action-description text (`（…）`, `*…*`, whatever your app uses) from *older* turns only, keeping actual dialogue intact — the differentiator | off (format-sensitive, opt-in) |
| `max_prompt_tokens` | Hard ceiling on the whole prompt: drops the oldest turns until the estimate fits | off (opt-in) |

### A ceiling, not just a shape: `max_prompt_tokens`

Every strategy above is *structural*. "Keep the last 6 turns" says nothing
about how big those turns are, so on its own the compressed output has no
upper bound.

That gap is not theoretical. The extractive trim in `history_window` splits
on sentence-ending punctuation, and short chat lines often have none —
`"ok"`, `"在吗"`, `"lol"`. With fewer than two sentences to work with it
returns the text unchanged. On 40 turns of that traffic, compression came
to **1.1%**.

```python
CompressorConfig(keep_recent_turns=4, max_prompt_tokens=800)
```

Same input, same trim: **90.0%**, and the result is guaranteed under 800
tokens.

Whole turns are dropped, never individual messages — turns break at each
new user message, so an assistant `tool_calls` block and the `tool`
results answering it always live in the same turn. Per-message pruning
could strand a `tool_calls` with no matching results, which providers
reject outright.

Three things are never sacrificed to meet the budget: the cache-stable
prefix, the most recent `budget_min_recent_turns` turns (dropping the
pending question deletes the request itself), and recurring system
messages, which are re-attached afterwards. A budget that can't be met is
logged and left unmet rather than honoured by breaking the prompt — and it
never raises, because a proxy mid-request shouldn't 500 over an
unrealistic ceiling.

Off by default. A config that doesn't mention it behaves byte-for-byte as
it did before.

### Bring your own summarizer

The rules here are cheap and predictable but blunt — see the 1.1% above.
The honest options were to take on an ML dependency and start rewriting
characters' dialogue, or to let you do the condensing. This is the second:

```python
def my_summarizer(messages: list[dict]) -> str:
    # Your existing memory layer, an LLM call, anything you like.
    return summarize(messages)

config = CompressorConfig(
    keep_recent_turns=4,
    history_window_mode="summarize",
    summarizer=my_summarizer,
)
```

Every message older than `keep_recent_turns` goes to your callable, and
the whole block is replaced by the single string it returns. Apps in this
space usually already have a memory/summary layer whose output beats
anything a regex could produce.

On 50 turns of punctuation-free chat: `trim` 2.7%, `drop` 92.6%,
`summarize` **92.2%** — but `drop` gets there by discarding the history
outright, while `summarize` keeps a condensed memory of it. For a
companion bot that difference is the entire point.

Your callback is allowed to fail. It's usually a network call:

| Your callback | What happens |
|---|---|
| Raises (timeout, API error) | Logged, falls back to `trim`. The request still goes through. |
| Returns `""` | Read as "this history isn't worth keeping" — the block is dropped |
| Returns a non-string | Logged, falls back to `trim` |
| Nothing has aged out yet | Not called at all — no spending a request to summarize an empty block |

Python-only by nature: a callable can't come from TOML, so setting
`summarizer` in a config file is rejected with an explanation rather than
failing later inside compression.

## Benchmarks

Take a 500-turn roleplay conversation — persona prefix, Chinese dialogue
with stage directions, repeated per-turn footers. That's 37,176 tokens
going to the LLM on every request.

| Config | Tokens after | Saved |
|---|---|---|
| No compression | 37,176 | — |
| Default (zero tuning) | 31,041 | 16.5% |
| + stage-direction stripping | 19,545 | **47.4%** |

The persona prefix is never touched — the 47% comes entirely from the
dialogue history, where it doesn't change how the character talks.

The same pattern holds across conversation lengths:

| Turns | Before | Default | + strip |
|---|---|---|---|
| 50 | 3,966 | 3,366 (15.1%) | 2,355 **(40.6%)** |
| 200 | 15,036 | 12,591 (16.3%) | 8,085 **(46.2%)** |
| 500 | 37,176 | 31,041 (16.5%) | 19,545 **(47.4%)** |

Savings *improve* at scale — more turns fall outside the
`keep_recent_turns` window, so more content gets compressed.

For apps that already run their own memory layer,
`history_window_mode="drop"` + `keep_recent_turns=3` pushes past 90%.

Run it yourself: `python benchmark/run_benchmark.py`.

## When to use / when to skip

| Use roleplay-slim if you… | Skip it if you… |
|---|---|
| Run a character/roleplay bot with long chat history | Only make single-turn requests (no history to compress) |
| Already pay for token-based LLM pricing per request | Are on a flat-rate or unlimited-token plan |
| Have a fixed persona/config prefix you want to cache | Have no cache-stable prefix at all |
| Send dialogue-heavy content (prose, not JSON) | Primarily send structured data / JSON / tool output |
| Want a drop-in proxy with zero code changes | Need MCP, multi-provider format translation, or a GUI |

## How it compares to alternatives

| | roleplay-slim | [headroom](https://github.com/headroomlabs-ai/headroom) | [kompact](https://github.com/npow/kompact) | [KrunchWrapper](https://github.com/naemlucifer/KrunchWrapper) |
|---|---|---|---|---|
| **Built for** | Prose dialogue | Agent tool output, JSON, code | Multi-step agentic traces | General text |
| **Prefix cache-safe** | ✅ Byte-for-byte guarantee | ✅ CacheAligner (reconstructs) | ⚠️ Restores after compression | ❌ No concept of prefix |
| **Dialogue-aware** | ✅ Stage directions, footers | ❌ Generic text only | ❌ Generic text only | ❌ Generic text only |
| **Depends on ML** | No (pure rules) | Yes (Kompress-v2-base) | No (heuristics) | No (heuristics) |
| **Proxy mode** | ✅ OpenAI Chat Completions | ✅ OpenAI + Anthropic | ✅ OpenAI | ✅ OpenAI |
| **MCP server** | ❌ | ✅ | ❌ | ❌ |
| **Reversible** | ❌ (lossy trim/strip) | ✅ (CCR) | ❌ | ❌ |
| **Install size** | < 5 MB | 500+ MB (ONNX + model) | < 10 MB | < 5 MB |

**Explicitly out of scope for v0.2** (see the plan doc if you're
contributing): no LLMLingua-style ML-based semantic compression, no
multi-provider wire-format translation (OpenAI format only — covers
DeepSeek and most others), no cross-request semantic cache/vector store,
no GUI.

---

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
  that already does this (see `examples/example_config.toml`'s comment).

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

---

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

---

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

### Check it against your own data first

This is a lossy transform on your production prompts, and nothing above
tells you what it will do to *your* conversations — whether `trim` touches
your messages at all, how much of the prompt your prefix already occupies,
which turns a budget would drop. So look before you switch it on:

**Your prefix share sets the ceiling.** The prefix is never compressed, by
design — so whatever fraction of your prompt it occupies is a fraction
compression can't touch. An app with a long persona and a short recent
history has very little compressible content left, and no amount of tuning
will change that; an app with a short prefix and hundreds of turns behind it
has almost all of it available. The benchmark figures above come from the
second shape. Before you judge the numbers you get, check which shape you
have — `preview` reports the prefix share directly (`~73 tokens (17% of the
prompt)` in the output below).

```bash
roleplay-slim preview conversation.json --keep-recent-turns 2
```

```
========================================================================
roleplay-slim preview
========================================================================
  messages       25  ->    18
  tokens~       425  ->   273   saved 152 (35.8%)
  turns           8

  prefix     1 message(s), ~73 tokens (17% of the prompt) — passed through unchanged

------------------------------------------------------------------------
resulting prompt (18 messages)
------------------------------------------------------------------------
=   system    You are Mutsumi Wakaba, a quiet guitarist. You are Mutsumi…   [prefix]
+ user      Question 0. …… And a final sentence.
+ assistant Answer 0. …… Closing thought.
...
= user      Question 7. This is a first sentence. This is a middle one. …
= system    [FORMAT] always end with a tag

------------------------------------------------------------------------
gone from the original (12 messages)
------------------------------------------------------------------------
- user      Question 0. This is a first sentence. This is a middle one. …
...
```

Takes a messages array or a full captured request body, from a file or
stdin, so a payload dumped straight off the wire works unedited. Makes no
network calls and writes no files. `--json` emits the compressed messages
for piping somewhere else; `--quiet` gives you the summary alone.

It deliberately does **not** claim a one-to-one mapping between original
and compressed messages, because none reliably exists — `trim` rewrites in
place, `summarize` collapses a block into one new message, dedupe removes
copies from arbitrary positions. It reports what can be established
without guessing: whether a piece of content survives verbatim, and what
the resulting prompt actually looks like.

### See the difference

**Without compression** — every request carries the full weight of history:

```
[system] You are Aria, a shy guitarist. Stay in character. ...（380 tokens）
[system] [Session context] Format rules apply.
[user] （推开练习室的门）今天来得好早啊...（turn 1）
[assistant] （抬头看了一眼）还没呢...（turn 1）
[system] [FORMAT RULE] End your reply with a mood tag.
...（196 more turns, same footer repeated every turn）
```
→ 15,036 tokens to the LLM, every request.

**With roleplay-slim** — same conversation, example config:

```
[system] You are Aria, a shy guitarist. Stay in character. ...（380 tokens）
[system] [Session context] Format rules apply.
[user] （推开练习室的门）今天来得好早啊...（turn 1）
[assistant] （抬头看了一眼）还没呢...（turn 1）
...（turns 2-195: first+last sentence only, stage directions stripped）
[user] （竖起大拇指）进步巨大...（turn 200, recent — kept verbatim）
[assistant] （摇摇头）今天不练了...（turn 200, recent — kept verbatim）
[system] [FORMAT RULE] End your reply with a mood tag.（only once）
```
→ 8,085 tokens. Same character, same conversation, **46% less**.

### 30-second tryout

```bash
pip install "roleplay-slim[all]"
export UPSTREAM_API_KEY=sk-your-real-key
roleplay-slim-proxy --config examples/example_config.toml
```

Then point any OpenAI-compatible client at `http://127.0.0.1:8791/v1`:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8791/v1", api_key="not-used")
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=your_messages,  # <-- compressed before reaching the LLM
)
```

You'll see a line like this in your terminal on every request:

```
[roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
```

---

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
roleplay-slim-proxy --config examples/example_config.toml
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

#### Proof, not just estimates

Those figures are *estimates* — `cl100k_base` run over text bound for
whatever provider you're actually using. `/stats` also reports what the
provider itself said:

```json
{
  "request_count": 3,
  "tokens_before_total": 19263,
  "tokens_after_total": 19083,
  "savings_pct": 0.93,
  "upstream": {
    "usage_sample_count": 3,
    "prompt_tokens_total": 2700,
    "completion_tokens_total": 126,
    "cache_hit_tokens_total": 1536,
    "cache_miss_tokens_total": 1164,
    "cache_hit_pct": 56.89
  }
}
```

`cache_hit_pct` is the one that matters. This project's central claim is
that leaving the prefix byte-identical keeps the provider's prefix cache
hitting — and now that's a number you can read rather than a promise you
have to take on faith.

Both sets of figures are useful and neither replaces the other: the
estimate covers the compression delta (the provider never sees the
uncompressed version), the `upstream` block covers what you were actually
billed for.

**Don't read `savings_pct` as a cost figure.** `cl100k_base` is OpenAI's
tokenizer, and on CJK text it counts substantially more tokens than
providers with their own tokenizers actually bill — measured against
DeepSeek's reported `prompt_tokens`, the local estimate ran well over 50%
high on Chinese dialogue. Both `tokens_before_total` and
`tokens_after_total` are inflated by roughly the same factor, so the
*ratio* stays meaningful; the absolute numbers, and any money you try to
derive from them, do not. Use the `upstream` block for anything you care
about being right.

The block is `null` until something has been measured, rather than a row
of zeroes — "not measured" and "measured zero" are different claims.
`cache_*` fields stay `null` on providers that don't report a cache
breakdown (OpenAI among them) instead of being flattened into a
misleading 0% hit rate. Streaming responses aren't counted: they only
carry usage when the caller sets `stream_options.include_usage`.

See `examples/example_config.toml` for a config modeled on a real
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

---

## API coverage

| | |
|---|---|
| ✅ `POST /v1/chat/completions` | Full support — both regular and streaming |
| ✅ `GET /v1/models`, `/v1/models/{id}` | Forwarded verbatim, uncompressed — clients like SillyTavern and OpenWebUI fetch this on connect to populate their model picker |
| ✅ Any other `/v1/*` endpoint | Forwarded verbatim (embeddings, etc.). Nothing outside chat completions carries a `messages` array, so nothing outside it is compressed |
| ✅ `system` / `user` / `assistant` roles | Full support |
| ✅ Streaming (SSE) | Transparent pass-through |
| ✅ OpenAI-compatible client libraries | Drop-in `base_url` swap |
| ⚠️ `developer` role | Supported in compression, not yet tested against every provider's wire format |
| ⚠️ Multimodal `content` (list of parts) | Passes through unmodified; token estimation counts text parts only |
| ⚠️ Tool calls (`tool_calls` → `tool` → `assistant`) | Chains are preserved atomically, but the proxy itself doesn't execute tools |
| ❌ `POST /v1/responses` | Not supported (Chat Completions only) |
| ❌ Function execution | The proxy compresses messages; it doesn't run your tools |

## Status

v0.3 — built and dogfooded against one production roleplay bot's traffic.
167 tests, including Hypothesis property tests over generated message
shapes.

Contributions welcome, especially additional `stage_direction_pattern`
presets for other apps' conventions.

## License

MIT
