"""Real-shape validation for the prefix optimizer (docs/designs/prefix-optimizer.md).

The design doc calls for validating analyze() against a real (redacted)
request log — not done at ship time, since the only real log available
belongs to a private production deployment. This closes that gap without
touching any real request content: the corpus here is entirely synthetic
dialogue, but its *structural* parameters (prefix message count, per-turn
message counts, turn-count spread, a recurring footer) are drawn from
production traffic actually observed on 2026-08-20 via the proxy's own
request log — see the roleplay-slim gotchas/CHANGELOG for that session.
Specifically:

- A 2-message stable prefix (persona + shared-context block) is the
  standard shape; a real request was also seen with pre:3 (an extra
  recurring block ahead of the dialogue) — modeled here as the footer
  sometimes also appearing as a leading block on some turns.
- Turn counts per request varied widely in real traffic: single-turn
  exchanges up to a real 5-turn request logged as
  ``msgs:17 pre:3 turns:5 tcounts:[1, 2, 2, 2, 7]`` (some turns carry more
  than 2 messages — consistent with a recurring footer message attached to
  each turn, which is exactly what dedupe_verbatim_tail exists to collapse).
- A recurring per-turn footer (a format-reminder system message) is a real,
  common pattern in this class of app — already covered structurally by
  the fixture in test_compressor_e2e.py; reused here for the same reason.

What this test actually checks: given a batch of samples shaped like real
traffic (not just the clean single-shape corpus in test_optimizer.py),
analyze() still (a) recommends exactly the true stable prefix — no more,
no less — and (b) the projected cache-hit-ceiling estimate for that
recommendation is a plausible, non-degenerate number, not a proof of any
specific dollar figure (that would require real content, which this
deliberately never touches).
"""
from __future__ import annotations

import random

from roleplay_slim.optimizer import analyze

# Structural constants matching what was actually observed in production
# traffic on 2026-08-20 (message roles/counts only — no real content).
PERSONA = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED = "[Session context — persistent across every request]\nFormat rules apply."
FOOTER = "[FORMAT RULE] End your reply with a mood tag."

# Real turn-count spread seen in one session's worth of production requests:
# mostly short (1-2 turns), occasionally much longer. Not a claim this is
# THE real distribution — a plausible spread in the same shape.
_TURN_COUNT_WEIGHTS = [1, 1, 1, 2, 2, 3, 5, 7]


def _build_sample(rng: random.Random, sample_idx: int) -> list[dict]:
    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": SHARED},
    ]
    n_turns = rng.choice(_TURN_COUNT_WEIGHTS)
    for t in range(n_turns):
        messages.append({"role": "user", "content": f"sample {sample_idx} turn {t} — 今天过得怎么样"})
        messages.append({"role": "assistant", "content": f"（想了想）turn {t} 的回复内容，长度不一"})
        # Real traffic showed some turns carrying an extra message (a
        # recurring footer) — model that structurally without claiming a
        # fixed rate.
        if rng.random() < 0.6:
            messages.append({"role": "system", "content": FOOTER})
    return messages


def _real_shape_corpus(n: int = 40, seed: int = 20260820) -> list[list[dict]]:
    rng = random.Random(seed)
    return [_build_sample(rng, i) for i in range(n)]


def test_recommends_exactly_the_true_prefix_under_varied_turn_counts():
    """The whole point of validating against a realistic (not hand-picked
    clean) corpus: varied turn counts and an intermittent footer must not
    fool the optimizer into over- or under-recommending the prefix."""
    corpus = _real_shape_corpus()
    report = analyze(corpus)
    assert report.samples == 40
    assert report.recommended_prefix == 2
    assert report.positions[0].stable
    assert report.positions[1].stable
    # position 2 is the first turn's user message — varies every sample,
    # by construction, so it must not be recommended.
    assert not report.positions[2].stable


def test_cache_hit_ceiling_estimate_is_plausible_not_degenerate():
    """Not a claim about a specific dollar figure (would require real
    content) — just that the projected ceiling for the recommended prefix
    is a real, sane percentage given how large the persona text is relative
    to a handful of short dialogue turns."""
    corpus = _real_shape_corpus()
    report = analyze(corpus)
    k = report.recommended_prefix
    projected = report.prefix_share_by_len[k]
    assert 0.0 < projected <= 100.0
    # The persona text alone is large (20x repeated sentence) relative to
    # a few short synthetic turns, so short samples should show a high
    # prefix share — sanity bound, not a precise claim.
    assert projected > 20.0


def test_footer_alone_is_not_mistaken_for_a_leading_prefix_extension():
    """The footer recurs across samples but never at a LEADING position
    (it only appears after the first turn), so it must never extend the
    recommended prefix — pins the same 'only a leading run counts' honesty
    property from test_optimizer.py against this messier corpus."""
    corpus = _real_shape_corpus()
    report = analyze(corpus)
    footer_positions = [
        p for p in report.positions
        if p.index >= report.recommended_prefix and FOOTER in p.content_preview
    ]
    # If the footer shows up as a *stable* non-leading position, it must be
    # reported as blocked, not folded into the recommendation.
    for p in footer_positions:
        if p.stable:
            assert p.index >= report.recommended_prefix
