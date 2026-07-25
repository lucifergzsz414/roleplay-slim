# roleplay-slim

Dialogue/roleplay-aware LLM context compression — a library and an
OpenAI-compatible proxy that knows the difference between a **cache-stable
persona prefix** and the **dialogue history you pay for on every request**.

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

## What it compresses (v0.1, all rule-based — no ML model, no lossy
semantic scoring)

| Strategy | What it does | Default |
|---|---|---|
| `whitespace_normalize` | Collapses redundant blank lines/whitespace | on |
| `dedupe_verbatim_tail` | If the exact same footer/reminder text repeats across turns, keeps only the last copy | on |
| `history_window` | Keeps the most recent N turns verbatim; older turns get dropped or trimmed to a first+last-sentence stub | on |
| `strip_stage_directions` | Removes parenthetical/action-description text (`（…）`, `*…*`, whatever your app uses) from *older* turns only, keeping actual dialogue intact — the differentiator | off (format-sensitive, opt-in) |

**Explicitly out of scope for v0.1** (see the plan doc if you're
contributing): no LLMLingua-style ML-based semantic compression, no
multi-provider wire-format translation (OpenAI format only — covers
DeepSeek and most others), no cross-request semantic cache/vector store,
no GUI.

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
```

## Use as a library

```python
from roleplay_slim import compress, CompressorConfig

config = CompressorConfig(
    keep_recent_turns=3,
    enable_strip_stage_directions=True,
    stage_direction_pattern=r"（[^）]*）",  # match your app's convention
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

See `examples/qqbot_style_config.toml` for a config modeled on a real
production roleplay bot's message structure.

## Status

v0.1 — built and dogfooded against one production roleplay bot's traffic.
Contributions welcome, especially additional `stage_direction_pattern`
presets for other apps' conventions.

## License

MIT
