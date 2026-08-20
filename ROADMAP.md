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
| Real-shape validation for the prefix optimizer (`tests/test_optimizer_real_shape.py`) — synthetic corpus matching real observed traffic structure, no real content | [`prefix-optimizer.md`](docs/designs/prefix-optimizer.md) | 0.4.1 |
| `StatsStore` usage-attribution race fix — see CHANGELOG | — | 0.4.1 |
| Semantic-cache design review — original proposal conflated two things; the money-saving half (caching LLM replies) rejected outright, the CPU-saving half left undocumented pending real profiling evidence | [`semantic-cache.md`](docs/designs/semantic-cache.md) | Unreleased |
| Telegram adapter (`examples/telegram_bot.py`) and a generic OpenAI-compatible / OpenWebUI-style adapter (`examples/openwebui_style_adapter.py`) | — | Unreleased |

Each design doc records where the shipped implementation deliberately
diverged from the original proposal (usually: cutting scope that turned out
to be premature, or — for semantic-cache — rejecting most of the proposal
outright) — worth reading before proposing a bigger version of any of these.

## Proposed, not started

Nothing right now. Everything that was on this list has either shipped or
been resolved by design review (see Shipped, above, and each item's design
doc for what was cut and why). This section stays here as the place new
proposals go, not as a promise something is always pending.

Open to suggestions — see [CONTRIBUTING.md](CONTRIBUTING.md) for how a
larger feature proposal should start (a short design doc in
`docs/designs/`, same as everything above).

## Ongoing, not versioned

- **More framework adapters in `examples/`.** SillyTavern, a QQ bot,
  Telegram, and a generic OpenWebUI-style adapter exist. A native
  Discord.py `Cog`-based version and a proper async/production-grade
  Telegram bot (the current ones are library-mode skeletons with the real
  event-loop wiring commented out, by design — see each file's docstring)
  would be welcome contributions, but aren't blocking anything.

## Explicitly out of scope

- **Multi-provider protocol translation** (Anthropic format, etc.) and
  **upstream gateway features** (failover, rate limiting, key rotation
  across multiple providers). Both would turn this into "another OpenAI
  gateway" and dilute the actual differentiator (structure-aware
  compression), which is the reason to reach for this over a generic
  compressing proxy in the first place.
