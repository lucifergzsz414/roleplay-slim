# Roadmap

Status of what's shipped and what's proposed. Design docs for each item
(shipped or not) live in [`docs/designs/`](docs/designs/) — read those for
the reasoning, trade-offs, and what was deliberately cut from scope.

## Shipped

| Item | Design doc | Shipped in |
|---|---|---|
| Cross-request prefix optimizer (`optimize` CLI + `roleplay_slim.optimizer`) | [`prefix-optimizer.md`](docs/designs/prefix-optimizer.md) | 0.4.0 |
| Synthetic fidelity benchmark (token savings + fact retention) | [`benchmark-fidelity.md`](docs/designs/benchmark-fidelity.md) | 0.4.0 |
| `/stats` SQLite persistence (survives restarts) | [`stats-persistence.md`](docs/designs/stats-persistence.md) | 0.4.0 |
| Multi-client-token proxy auth (`client_auth_tokens_extra`) | — (see CHANGELOG) | 0.4.0 |
| CI: ruff lint + sdist packaging gate | — (see `gotchas.md`) | 0.4.0 |

Each design doc records where the shipped implementation deliberately
diverged from the original proposal (usually: cutting scope that turned out
to be premature) — worth reading before proposing a bigger version of any
of these.

## Proposed, not started

- **~~Semantic caching~~ — mostly rejected on design review.** The original
  framing here conflated two different things; see
  [`semantic-cache.md`](docs/designs/semantic-cache.md) for the split.
  Caching the *upstream LLM's reply* for repeated inputs (the original
  "companion-bot 在吗" motivation) is rejected outright — it would make a
  roleplay character give verbatim-identical responses to repeated
  questions, directly undermining the project's own persona-consistency
  pitch (consistent character ≠ canned lines). Caching `compress()`'s own
  output (a pure CPU optimization, no ML needed) is technically sound but
  has no evidence it's a real bottleneck — proxy deployments are dominated
  by network I/O, not compression CPU — so it stays unbuilt pending actual
  profiling data showing otherwise.
- **More framework adapters in `examples/`.** SillyTavern, a QQ bot,
  Telegram, and a generic OpenWebUI-style adapter now exist (see below).
  Discord already had a working example, not actually just a stub as this
  entry used to claim. A native Discord.py `Cog`-based version and a
  proper async/production-grade Telegram bot (the current ones are
  library-mode skeletons with the real event-loop wiring commented out)
  would still be welcome contributions.

## Shipped since 0.4.0

| Item | Shipped in |
|---|---|
| Real-shape validation for the prefix optimizer (`tests/test_optimizer_real_shape.py`) — synthetic corpus matching real observed traffic structure, no real content | 0.4.1 |
| `StatsStore` usage-attribution race fix (see CHANGELOG) | 0.4.1 |
| `semantic-cache.md` design review — most of the original proposal rejected, see above | Unreleased |
| Telegram adapter (`examples/telegram_bot.py`) and a generic OpenAI-compatible / OpenWebUI-style adapter (`examples/openwebui_style_adapter.py`) | Unreleased |

## Explicitly out of scope

- **Multi-provider protocol translation** (Anthropic format, etc.) and
  **upstream gateway features** (failover, rate limiting, key rotation
  across multiple providers). Both would turn this into "another OpenAI
  gateway" and dilute the actual differentiator (structure-aware
  compression), which is the reason to reach for this over a generic
  compressing proxy in the first place.
