"""Cross-request prefix optimization (diagnostic tool).

compress() answers "within THIS request, what is the cache-stable prefix?"
via segmenter.detect_prefix_length. This module answers the complementary
question across MANY requests: "which of my leading messages are stable
enough to hoist into my cache-stable prefix block?"

The two differ in a way that matters for money. A message can be stable
within one request yet rebuilt differently on the next (a timestamp
greeting), and a message the segmenter treats as dynamic can turn out
identical across every request (a footer the app appends fresh each time).
The first is not hoistable; the second is — and it is exactly the kind of
thing no single-request view can see.

The projected cache-hit ceiling relies on the relationship measured in
production: the provider's prompt-cache hit rate tracks the prefix's share
of the prompt. So "hoisting positions 0..k-1 into your prefix" is projected
to raise your ceiling to the token share of positions 0..k-1. This is an
estimate, reported as such — the tool never touches your app's config.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .segmenter import detect_prefix_length
from .stats import estimate_messages_tokens
from .strategies import content_key


@dataclass
class PositionStats:
    """One column of the cross-sample table — what happens at position
    ``index`` across all samples."""

    index: int
    appearances: int
    appearance_rate: float        # appearances / total_samples
    identical_rate: float | None  # most-common content / appearances; None when never present
    role: str | None              # role of the most common message at this position
    content_preview: str          # short identity of the most common message
    stable: bool


@dataclass
class OptimizationReport:
    samples: int
    positions: list[PositionStats]
    # Longest leading run of stable positions. Only the *leading* run is
    # recommended: a stable footer sitting after a varying message can't be
    # hoisted into a contiguous prefix without dragging the varying message
    # along with it, so the tool is honest about that (see test
    # test_stable_message_after_varying_does_not_extend).
    recommended_prefix: int
    # Segmenter-detected prefix share, averaged across samples — the
    # "current" cache-hit ceiling estimate before any hoisting.
    current_prefix_share_pct: float
    # prefix_share_by_len[k] = average token share of positions 0..k-1
    # across samples (index 0 → 0.0). The "what if I hoist to k" curve.
    prefix_share_by_len: list[float]


def _content_preview(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return f"<{len(content)} multimodal part(s)>"
    if content is None:
        calls = message.get("tool_calls") or []
        return f"<{len(calls)} tool_call(s)>" if calls else "<empty>"
    return " ".join(str(content).split())


def _validate_thresholds(min_appearance: float, min_identical: float) -> None:
    if not (0.0 <= min_appearance <= 1.0) or not (0.0 <= min_identical <= 1.0):
        raise ValueError("min_appearance and min_identical must be between 0 and 1")


def analyze(
    samples: list[list[dict]],
    min_appearance: float = 0.90,
    min_identical: float = 0.95,
) -> OptimizationReport:
    """Analyze cross-request stability of leading message positions.

    A position is "stable" only when it appears in at least
    ``min_appearance`` of samples AND its content is byte-identical in at
    least ``min_identical`` of those appearances — a high appearance rate
    with content that changes every time (a timestamp) has no cache value.
    """
    if not samples:
        raise ValueError("analyze() needs at least one sample")
    _validate_thresholds(min_appearance, min_identical)
    total = len(samples)
    max_len = max(len(s) for s in samples)

    positions: list[PositionStats] = []
    for i in range(max_len):
        present = [s[i] for s in samples if len(s) > i]
        appearances = len(present)
        appearance_rate = appearances / total
        if not present:
            positions.append(
                PositionStats(i, 0, appearance_rate, None, None, "<absent>", False)
            )
            continue

        counts: Counter[str] = Counter()
        for m in present:
            counts[content_key(m.get("content", ""))] += 1
        top_key, top_count = counts.most_common(1)[0]
        identical_rate = top_count / appearances
        top_message = next(
            m for m in present if content_key(m.get("content", "")) == top_key
        )
        stable = appearance_rate >= min_appearance and identical_rate >= min_identical
        positions.append(
            PositionStats(
                index=i,
                appearances=appearances,
                appearance_rate=appearance_rate,
                identical_rate=identical_rate,
                role=top_message.get("role"),
                content_preview=_content_preview(top_message),
                stable=stable,
            )
        )

    recommended = 0
    for p in positions:
        if p.stable:
            recommended += 1
        else:
            break

    # Token accounting in one pass per sample (cumulative prefix sums), so
    # the what-if curve costs O(samples × positions), not O(samples ×
    # positions²) from re-tokenizing each prefix.
    cum_sums: list[list[int]] = []
    totals: list[int] = []
    for s in samples:
        acc = 0
        cum: list[int] = []
        for m in s:
            acc += estimate_messages_tokens([m])
            cum.append(acc)
        cum_sums.append(cum)
        totals.append(acc)

    def share_for(k: int) -> float:
        shares: list[float] = []
        for cum, total in zip(cum_sums, totals):
            if total == 0:
                continue
            if k <= 0:
                prefix_tok = 0
            elif k > len(cum):
                prefix_tok = total
            else:
                prefix_tok = cum[k - 1]
            shares.append(prefix_tok / total)
        return (sum(shares) / len(shares) * 100) if shares else 0.0

    prefix_share_by_len = [share_for(k) for k in range(max_len + 1)]

    current_shares: list[float] = []
    for s in samples:
        n = detect_prefix_length(s)
        total_tok = estimate_messages_tokens(s)
        if total_tok == 0:
            continue
        current_shares.append(estimate_messages_tokens(s[:n]) / total_tok)
    current_share = (sum(current_shares) / len(current_shares) * 100) if current_shares else 0.0

    return OptimizationReport(
        samples=total,
        positions=positions,
        recommended_prefix=recommended,
        current_prefix_share_pct=round(current_share, 2),
        prefix_share_by_len=[round(v, 2) for v in prefix_share_by_len],
    )
