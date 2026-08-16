"""Tests for the fidelity benchmark harness (benchmark/run_fidelity.py).

Two things are pinned here:
1. The harness reports meaningful numbers — under a config that trims but
   keeps single-sentence user lines, planted facts survive; under a config
   that drops old turns, only facts inside the recent window survive.
2. The harness *detects* loss — the whole point of building it is that a
   future strategy change which quietly drops persona memory shows up here.
"""
from benchmark.corpus_gen import generate
from benchmark.run_fidelity import measure
from roleplay_slim import CompressorConfig

# 20 turns, a fact every 5 turns → fact turns {0,5,10,15}, plus the
# guaranteed final-turn fact at 19. keep_recent_turns=2 protects turns
# {18,19}, so exactly one fact (turn 19) sits in the recent window.
SAMPLE = generate(seed=20260814, turns=20, fact_interval=5)
FACT_TURNS = sorted(f.turn_index for f in SAMPLE.facts)
assert FACT_TURNS == [0, 5, 10, 15, 19], FACT_TURNS


def test_trim_mode_retains_all_single_sentence_facts():
    config = CompressorConfig(keep_recent_turns=2, history_window_mode="trim")
    result = measure(SAMPLE, config)
    assert result.fact_total == 5
    assert result.fact_retained == 5
    assert result.fact_retention_pct == 100.0


def test_drop_mode_keeps_only_recent_window_facts():
    config = CompressorConfig(keep_recent_turns=2, history_window_mode="drop")
    result = measure(SAMPLE, config)

    # The 4 facts planted in turns {0,5,10,15} are all inside the aged-out
    # region and get dropped wholesale; the final-turn fact (turn 19) is in
    # the protected recent window and survives.
    assert result.fact_total == 5
    assert result.fact_retained == 1
    assert result.lost_facts == ["f0", "f1", "f2", "f3"]
    kept = [f for f in SAMPLE.facts if f.id not in result.lost_facts]
    assert [f.id for f in kept] == ["f4"]
    assert kept[0].turn_index == 19


def test_harness_detects_loss():
    """The self-check that justifies building the harness: a config that
    drops old turns must register a retention drop — if this test ever sees
    retention==100% under drop, the harness is broken, not the config."""
    trim = measure(SAMPLE, CompressorConfig(keep_recent_turns=2, history_window_mode="trim"))
    drop = measure(SAMPLE, CompressorConfig(keep_recent_turns=2, history_window_mode="drop"))
    assert drop.fact_retention_pct < trim.fact_retention_pct
    assert drop.fact_retention_pct < 100.0


def test_measure_reports_token_and_prefix_figures():
    config = CompressorConfig(keep_recent_turns=2, history_window_mode="trim")
    result = measure(SAMPLE, config)
    assert result.tokens_before > result.tokens_after
    assert result.savings_pct > 0
    assert 0 < result.prefix_share_pct < 100
    assert result.sample_id == SAMPLE.id
