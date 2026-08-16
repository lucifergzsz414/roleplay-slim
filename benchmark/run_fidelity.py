"""Fidelity benchmark: how much planted persona memory survives compression.

run_benchmark.py reports token savings only. Savings alone can look great
while old turns are being dropped wholesale — that is exactly the failure
this measures. For every planted fact in a generated sample, does its
distinctive substring still appear in the compressed output?

Tier A only (zero-dependency, no LLM): substring survival. Fully
reproducible via ``--seed`` — the corpus generator is seeded and
deterministic, so these numbers can go in a README table the same way
run_benchmark.py's do.

Usage:
    python benchmark/run_fidelity.py [--seeds 1,2,3] [--turns 50]
        [--fact-interval 5] [--keep 2] [--mode trim|drop] [--json]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence

try:
    from benchmark.corpus_gen import Sample, generate_corpus
except ImportError:  # run as `python benchmark/run_fidelity.py`
    from corpus_gen import Sample, generate_corpus

from roleplay_slim import CompressorConfig, compress
from roleplay_slim.stats import estimate_messages_tokens

DEFAULT_SEEDS = [1, 2, 3]


@dataclasses.dataclass
class FidelityResult:
    sample_id: str
    tokens_before: int
    tokens_after: int
    savings_pct: float
    prefix_share_pct: float
    fact_total: int
    fact_retained: int
    fact_retention_pct: float
    lost_facts: list[str]


def measure(sample: Sample, config: CompressorConfig) -> FidelityResult:
    """Run compression over one sample and measure token + fact retention."""
    before = sample.messages
    after = compress(before, config)

    before_tok = estimate_messages_tokens(before)
    after_tok = estimate_messages_tokens(after)
    saved = before_tok - after_tok
    savings_pct = saved / before_tok * 100 if before_tok else 0.0

    prefix_tok = estimate_messages_tokens(before[: sample.prefix_len])
    prefix_share_pct = prefix_tok / before_tok * 100 if before_tok else 0.0

    after_text = "\n".join(
        m.get("content", "") if isinstance(m.get("content"), str) else ""
        for m in after
    )
    retained = [f for f in sample.facts if f.feature in after_text]
    lost = [f for f in sample.facts if f.feature not in after_text]

    return FidelityResult(
        sample_id=sample.id,
        tokens_before=before_tok,
        tokens_after=after_tok,
        savings_pct=round(savings_pct, 2),
        prefix_share_pct=round(prefix_share_pct, 2),
        fact_total=len(sample.facts),
        fact_retained=len(retained),
        fact_retention_pct=round(len(retained) / len(sample.facts) * 100, 2),
        lost_facts=[f.id for f in lost],
    )


def run(
    seeds: Sequence[int] = DEFAULT_SEEDS,
    turns: int = 50,
    fact_interval: int = 5,
    config: CompressorConfig | None = None,
) -> list[FidelityResult]:
    config = config or CompressorConfig()
    samples = generate_corpus(list(seeds), turns, fact_interval)
    return [measure(s, config) for s in samples]


def _print_report(results: list[FidelityResult]) -> None:
    print(
        f"{'sample':<14} {'before':>7} {'after':>7} {'saved%':>7} "
        f"{'prefix%':>8} {'facts':>6} {'kept':>5} {'retention%':>11}  lost"
    )
    print("-" * 80)
    for r in results:
        lost = ",".join(r.lost_facts) if r.lost_facts else "-"
        print(
            f"{r.sample_id:<14} {r.tokens_before:>7} {r.tokens_after:>7} "
            f"{r.savings_pct:>6.1f}% {r.prefix_share_pct:>7.1f}% "
            f"{r.fact_total:>6} {r.fact_retained:>5} {r.fact_retention_pct:>10.1f}%  {lost}"
        )

    n = len(results)
    avg_saved = sum(r.savings_pct for r in results) / n
    avg_prefix = sum(r.prefix_share_pct for r in results) / n
    total_facts = sum(r.fact_total for r in results)
    total_kept = sum(r.fact_retained for r in results)
    print("-" * 80)
    print(
        f"AVG  {avg_saved:.1f}% token savings · {avg_prefix:.1f}% prefix · "
        f"fact retention {total_kept}/{total_facts} "
        f"({total_kept / total_facts * 100:.1f}%)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="1,2,3", help="comma-separated seeds")
    parser.add_argument("--turns", type=int, default=50)
    parser.add_argument("--fact-interval", type=int, default=5)
    parser.add_argument("--keep", type=int, default=6, help="keep_recent_turns")
    parser.add_argument(
        "--mode", choices=["trim", "drop", "summarize"], default="trim"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    config = CompressorConfig(
        keep_recent_turns=args.keep, history_window_mode=args.mode
    )
    results = run(seeds, args.turns, args.fact_interval, config)

    if args.json:
        print(json.dumps([dataclasses.asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        _print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
