"""One-off profiling run for docs/designs/semantic-cache.md, Direction A.

That design doc explicitly declined to add a result cache for compress()
without real profiling data proving it's a hot-path bottleneck. This script
provides that data: it times compress() over a corpus shaped like real
observed production traffic (same structural parameters as
tests/test_optimizer_real_shape.py — mostly 1-2 turn requests, occasionally
up to 7, intermittent recurring footer) and reports the distribution,
compared against typical upstream LLM round-trip latency for scale.

No real request content is used — purely synthetic dialogue, same as the
existing test fixture. Not part of the pytest suite; run manually:

    python benchmark/profile_compress.py
"""
from __future__ import annotations

import random
import statistics
import time

from roleplay_slim.compressor import compress
from roleplay_slim.config import CompressorConfig

PERSONA = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED = "[Session context — persistent across every request]\nFormat rules apply."
FOOTER = "[FORMAT RULE] End your reply with a mood tag."

# Same spread as tests/test_optimizer_real_shape.py — mirrors turn counts
# actually observed in production traffic on 2026-08-20.
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
        if rng.random() < 0.6:
            messages.append({"role": "system", "content": FOOTER})
    return messages


def _real_shape_corpus(n: int, seed: int = 20260820) -> list[list[dict]]:
    rng = random.Random(seed)
    return [_build_sample(rng, i) for i in range(n)]


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * pct))
    return sorted_vals[idx]


def main() -> None:
    n_samples = 2000
    corpus = _real_shape_corpus(n_samples)
    config = CompressorConfig()  # defaults: whitespace_normalize + dedupe + history_window on

    # Warm up (import/JIT-adjacent effects, first-call file/regex compile costs)
    for messages in corpus[:50]:
        compress(messages, config)

    durations_us: list[float] = []
    for messages in corpus:
        start = time.perf_counter()
        compress(messages, config)
        durations_us.append((time.perf_counter() - start) * 1_000_000)

    durations_us.sort()
    mean_us = statistics.mean(durations_us)
    p50 = _percentile(durations_us, 0.50)
    p95 = _percentile(durations_us, 0.95)
    p99 = _percentile(durations_us, 0.99)
    worst = durations_us[-1]

    print(f"compress() over {n_samples} real-shaped samples:")
    print(f"  mean: {mean_us:.1f} us")
    print(f"  p50:  {p50:.1f} us")
    print(f"  p95:  {p95:.1f} us")
    print(f"  p99:  {p99:.1f} us")
    print(f"  max:  {worst:.1f} us")

    # Context: a real upstream LLM round trip (network + inference) is
    # typically low-single-digit seconds for a conversational reply.
    reference_llm_round_trip_s = 2.0
    ratio_pct = (p99 / 1_000_000) / reference_llm_round_trip_s * 100
    print(
        f"\nFor scale: a ~{reference_llm_round_trip_s:.0f}s upstream LLM round trip "
        f"is {1_000_000 * reference_llm_round_trip_s / p99:.0f}x the p99 compress() cost "
        f"({ratio_pct:.4f}% of total request latency)."
    )


if __name__ == "__main__":
    main()
