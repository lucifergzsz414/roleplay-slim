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

- **Semantic caching for the dynamic region.** Exact- or near-duplicate
  requests (a companion-bot "在吗" pattern) could return a cached compressed
  result instead of re-compressing every time. Deliberately deferred until
  the fidelity benchmark existed, so a caching layer can be checked against
  it rather than shipped on faith — that benchmark exists now (see above).
  Breaks the project's "zero ML dependency" purity if it needs embeddings for
  near-duplicate matching, so it should ship as an opt-in extra, not a
  default-on behavior.
- **More framework adapters in `examples/`.** SillyTavern and a QQ bot
  adapter exist; Telegram, Discord (beyond the stub), and a generic
  OpenWebUI-style adapter would each be a small, low-risk PR.

## Shipped since 0.4.0

| Item | Shipped in |
|---|---|
| Real-shape validation for the prefix optimizer (`tests/test_optimizer_real_shape.py`) — synthetic corpus matching real observed traffic structure, no real content | 0.4.1 |
| `StatsStore` usage-attribution race fix (see CHANGELOG) | 0.4.1 |

## Explicitly out of scope

- **Multi-provider protocol translation** (Anthropic format, etc.) and
  **upstream gateway features** (failover, rate limiting, key rotation
  across multiple providers). Both would turn this into "another OpenAI
  gateway" and dilute the actual differentiator (structure-aware
  compression), which is the reason to reach for this over a generic
  compressing proxy in the first place.
