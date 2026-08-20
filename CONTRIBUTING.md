# Contributing to roleplay-slim

## Setup

```bash
git clone https://github.com/lucifergzsz414/roleplay-slim.git
cd roleplay-slim
pip install -e ".[dev,proxy,tokens]"
```

## Before opening a PR

Run what CI runs, in this order:

```bash
ruff check src/roleplay_slim tests benchmark examples scripts
pytest -v
mypy src
```

If you touched packaging (`pyproject.toml`, anything under `src/` or the
sdist allowlist), also run:

```bash
python scripts/check_sdist.py
```

This builds the sdist and fails if any file ships outside the allowlist in
`pyproject.toml`'s `[tool.hatch.build.targets.sdist]`. That allowlist exists
because 0.3.1 accidentally shipped unrelated local scripts and a test cache
— see `gotchas.md` if you're curious about the history. A new top-level file
does not ship by default; if you add one that genuinely belongs in the
published package, add it to the allowlist explicitly.

## Code style

`ruff` runs an explicit rule set (`F`, `I`, `UP`, `E4`/`E7`/`E9`, `RUF022`),
not ruff's defaults — see the comment above `[tool.ruff.lint]` in
`pyproject.toml` for why (some default rules flag deliberate patterns, like
the proxy's broad `except Exception` around a live request path). Match
that rule set rather than reaching for a stricter one.

Docstrings and inline comments in this codebase tend to explain *why*, not
*what* — especially at points where a bug was fixed. If you fix something
non-obvious, a short comment on why the old code was wrong is more useful
to the next contributor than removing the evidence.

## What's in scope

- `src/roleplay_slim/` — the library and proxy.
- `tests/` — fixture tests, Hypothesis property tests (`test_properties.py`),
  and CLI tests.
- `benchmark/` — the synthetic fidelity benchmark (`corpus_gen.py` +
  `run_fidelity.py`) and the savings benchmark (`run_benchmark.py`). No real
  user data ever goes in here — only synthetic, seeded corpora.
- `examples/` — adapter examples for real chat frameworks (SillyTavern, a
  QQ bot, Discord, etc.). More of these — especially for frameworks not yet
  covered — are welcome and low-risk to review.
- `docs/designs/` — design docs for larger features before they're built.
  See `ROADMAP.md` for what's shipped and what's proposed.

## What's out of scope

`integrations/pet-installer/` is private desktop-pet distribution tooling
for one specific deployment — it was moved out of the repo root in 0.4.0
precisely so it reads as separate from the library. PRs touching it will
likely be declined; if you're building your own distribution tooling around
roleplay-slim, `examples/` is a better place to contribute a template others
can use.

## Reporting a bug

Open an issue with:

- The `roleplay-slim` version (`pip show roleplay-slim`).
- Your `CompressorConfig` (or the relevant TOML section) — synthetic values
  are fine, we don't need your persona text.
- A minimal message array that reproduces the issue, if you can construct
  one. If the bug depends on your specific content shape and you can't share
  it, describe the shape (roles, repeat patterns, multimodal content) instead.

## Releasing (maintainers)

Version bumps follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [SemVer](https://semver.org/) (with the `0.x` carve-out — breaking
changes can land in a minor release while the major version is `0`).
`CHANGELOG.md` is the source of truth for what's in each release; write the
entry as part of the same PR that bumps `version` in `pyproject.toml`.

Build and publish with `hatch`:

```bash
hatch build -t wheel -t sdist
python scripts/check_sdist.py    # verify before publishing — see above
hatch publish -u __token__       # prompts for the PyPI token interactively
```
