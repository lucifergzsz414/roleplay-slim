# v0.2.0 — First Public Release

roleplay-slim is a lightweight context optimization layer for persistent AI
characters. It's both a Python library (`compress(messages)`) and a drop-in
OpenAI-compatible proxy — swap your `base_url` and every request gets
compressed automatically.

## Why this exists

Most LLM context-compression tools (headroom, kompact, KrunchWrapper) are
built for JSON, logs, and code. They don't know what a persona prefix is,
and they can't tell stage directions from dialogue. roleplay-slim is built
specifically for prose dialogue — the kind a roleplay companion bot,
character.ai-style app, or long-running chat system actually sends.

## What it does

- **Cache-aware:** Auto-detects the cache-stable persona prefix and leaves it
  byte-for-byte untouched — hits DeepSeek/OpenAI prompt cache every time
- **Dialogue-native compression:** Whitespace normalize, footer dedup, old-turn
  trimming, and stage-direction stripping — all rule-based, zero ML dependency
- **Library + proxy:** `pip install` and import, or swap `base_url` and forget
  about it
- **Install size < 5 MB** — no ONNX, no model download

## By the numbers

| Turns | Default | + stage-direction stripping |
|---|---|---|
| 50 | 15.1% | **40.6%** |
| 200 | 16.3% | **46.2%** |
| 500 | 16.5% | **47.4%** |

Persona prefix: 100% preserved in all configurations.

## Quick start

```bash
pip install "roleplay-slim[all]"
export UPSTREAM_API_KEY=sk-your-key
roleplay-slim-proxy --config examples/example_config.toml
```

Point any OpenAI-compatible client at `http://127.0.0.1:8791/v1`.

## What's new in v0.2

- Rule-based compression (4 strategies, all configurable)
- OpenAI Chat Completions proxy with streaming support
- Token estimation with optional tiktoken backend
- Hypothesis-based property tests (87 tests)
- Multi-scale benchmarks (50/200/500 turns)
- QQ bot and Discord bot example adapters
- Full Chinese README (README_CN.md)

## What's NOT in scope

No ML model, no semantic scoring, no multi-provider format translation, no
MCP, no GUI, no vector store. roleplay-slim is a focused tool that does one
thing: protect the persona, compress the dialogue.
