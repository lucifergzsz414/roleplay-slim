"""Tests for the synthetic corpus generator (benchmark/corpus_gen.py).

The generator's whole job is to be a reliable yardstick: deterministic,
with facts placed in the compressible region and distinctive enough that a
substring check is meaningful. These tests pin those properties."""
from benchmark.corpus_gen import generate

SAMPLE = generate(seed=20260814, turns=20, fact_interval=5)


def test_deterministic_generation():
    a = generate(seed=42, turns=30, fact_interval=4)
    b = generate(seed=42, turns=30, fact_interval=4)
    assert a.messages == b.messages
    assert [f.feature for f in a.facts] == [f.feature for f in b.facts]


def test_different_seed_different_sample():
    a = generate(seed=1, turns=20, fact_interval=5)
    b = generate(seed=2, turns=20, fact_interval=5)
    assert a.messages != b.messages


def test_prefix_shape():
    # Two leading system messages (persona + shared block) before any
    # dialogue — the cache-stable prefix the compressor must preserve.
    assert SAMPLE.messages[0]["role"] == "system"
    assert SAMPLE.messages[1]["role"] == "system"
    assert SAMPLE.messages[2]["role"] == "user"
    assert SAMPLE.prefix_len == 2


def test_facts_live_in_dynamic_region_not_prefix():
    prefix_text = "".join(m.get("content", "") for m in SAMPLE.messages[: SAMPLE.prefix_len])
    for fact in SAMPLE.facts:
        # The fact's distinctive substring must be in the dynamic region...
        assert fact.feature in SAMPLE.messages[fact.turn_index * 3 + SAMPLE.prefix_len]["content"]
        # ...and never in the prefix (planting a fact in the prefix would be
        # cheating — the prefix is byte-identical by design).
        assert fact.feature not in prefix_text


def test_feature_appears_exactly_once_in_sample():
    all_text = "\n".join(m.get("content", "") for m in SAMPLE.messages)
    for fact in SAMPLE.facts:
        assert all_text.count(fact.feature) == 1, (
            f"{fact.feature!r} should appear exactly once; accidental "
            "duplicates would break the retention check"
        )


def test_fact_turn_index_points_at_carrying_message():
    # Message layout per turn: user, assistant, footer.
    for fact in SAMPLE.facts:
        msg = SAMPLE.messages[fact.turn_index * 3 + SAMPLE.prefix_len]
        assert msg["role"] == "user"
        assert fact.feature in msg["content"]


def test_final_turn_always_carries_a_fact():
    # The generator guarantees a fact in the last turn so the "recent"
    # bucket is never empty regardless of turn count — the fidelity tests
    # rely on being able to discriminate retained vs dropped by turn age.
    last_user_idx = max(
        i for i, m in enumerate(SAMPLE.messages) if m["role"] == "user"
    )
    last_fact_turn = max(f.turn_index for f in SAMPLE.facts)
    assert last_fact_turn * 3 + SAMPLE.prefix_len == last_user_idx
