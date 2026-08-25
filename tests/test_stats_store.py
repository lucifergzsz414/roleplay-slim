"""Unit tests for the SQLite-backed stats store (proxy/stats_store.py).

The store replaces the in-memory CompressionStats as the proxy's /stats
source of truth. The important properties: identical aggregation output,
usage back-fill, and — the reason it exists — totals surviving a fresh
instance on the same file (a proxy restart).
"""
from __future__ import annotations

import sqlite3

from roleplay_slim.proxy.stats_store import StatsStore

MSGS = [{"role": "user", "content": "hello there. second sentence."}]


def test_starts_empty(tmp_path):
    store = StatsStore(str(tmp_path / "s.db"))
    assert store.summary()["request_count"] == 0
    assert store.summary()["upstream"] is None
    store.close()


def test_record_accumulates(tmp_path):
    store = StatsStore(str(tmp_path / "s.db"))
    first = store.record(MSGS, [])
    second = store.record(MSGS, MSGS)  # no compression → saved == 0
    summary = store.summary()
    assert summary["request_count"] == 2
    assert summary["tokens_before_total"] == first["tokens_before"] + second["tokens_before"]
    assert summary["tokens_after_total"] == first["tokens_after"] + second["tokens_after"]
    assert summary["tokens_saved_total"] == (
        summary["tokens_before_total"] - summary["tokens_after_total"]
    )
    assert summary["savings_pct"] >= 0
    store.close()


def test_durable_across_instances(tmp_path):
    """Reopen the same file in a fresh store and the totals survive — this
    is the whole point of the file-backed store (proxy restarts)."""
    path = str(tmp_path / "d.db")
    s1 = StatsStore(path)
    s1.record(MSGS, [])
    s1.close()

    s2 = StatsStore(path)
    assert s2.summary()["request_count"] == 1
    assert s2.summary()["tokens_before_total"] > 0
    s2.close()


def test_memory_store_behaves_like_file():
    store = StatsStore(":memory:")
    store.record(MSGS, [])
    assert store.summary()["request_count"] == 1
    assert store.request_count == 1
    store.close()


def test_unwritable_path_degrades_to_memory_not_crash(caplog):
    """A db_path whose directory doesn't exist must fall back to in-memory
    stats with a warning, not raise — otherwise any deployment whose CWD
    isn't writable dies at startup (the 0.4.0 production incident)."""
    with caplog.at_level("WARNING", logger="roleplay_slim"):
        store = StatsStore("/nonexistent-dir-xyz/roleplay-slim-stats.db")
    store.record(MSGS, [])
    assert store.summary()["request_count"] == 1
    assert any("falling back to in-memory" in r.message for r in caplog.records)
    store.close()


def test_record_usage_backfills_latest_row(tmp_path):
    store = StatsStore(str(tmp_path / "u.db"))
    entry = store.record(MSGS, [])
    rec = store.record_usage(
        {
            "prompt_tokens": 1234,
            "completion_tokens": 56,
            "prompt_cache_hit_tokens": 512,
            "prompt_cache_miss_tokens": 722,
        },
        entry["id"],
    )
    assert rec["prompt_tokens"] == 1234
    upstream = store.summary()["upstream"]
    assert upstream["usage_sample_count"] == 1
    assert upstream["prompt_tokens_total"] == 1234
    assert upstream["completion_tokens_total"] == 56
    assert upstream["cache_hit_tokens_total"] == 512
    assert upstream["cache_miss_tokens_total"] == 722
    assert upstream["cache_hit_pct"] == round(512 / (512 + 722) * 100, 2)
    store.close()


def test_usage_is_null_before_any_provider_accounting(tmp_path):
    store = StatsStore(str(tmp_path / "n.db"))
    store.record(MSGS, [])
    # a request recorded but no usage block seen → upstream stays null
    assert store.summary()["upstream"] is None
    store.close()


def test_usage_with_no_recognisable_numbers_is_ignored(tmp_path):
    store = StatsStore(str(tmp_path / "x.db"))
    entry = store.record(MSGS, [])
    assert store.record_usage({"prompt_tokens": None}, entry["id"]) is None
    assert store.summary()["upstream"] is None
    store.close()


def test_usage_unparseable_field_types_are_dropped(tmp_path):
    """Same tolerance as CompressionStats: a wrong number is worse than a
    missing one when these figures back the cache claim."""
    store = StatsStore(str(tmp_path / "y.db"))
    entry = store.record(MSGS, [])
    store.record_usage(
        {
            "prompt_tokens": 40.0,
            "completion_tokens": None,
            "prompt_cache_hit_tokens": "lots",
        },
        entry["id"],
    )
    upstream = store.summary()["upstream"]
    assert upstream["prompt_tokens_total"] == 40
    assert upstream["completion_tokens_total"] == 0
    assert upstream["cache_hit_tokens_total"] is None
    store.close()


def test_record_returns_row_id():
    store = StatsStore(":memory:")
    e1 = store.record(MSGS, [])
    e2 = store.record(MSGS, [])
    assert e1["id"] != e2["id"]
    store.close()


def test_usage_attributes_to_the_correct_row_not_the_latest(tmp_path):
    """Regression test for the race this fixes: request A is recorded, then
    request B is recorded (interleaved, as happens under real concurrent
    requests), then A's usage comes back. Before row_id was required, this
    landed on whichever row was numerically highest (B's) instead of A's —
    found via two real near-simultaneous production requests, one ending up
    with no upstream data and the other with someone else's."""
    store = StatsStore(str(tmp_path / "race.db"))
    entry_a = store.record(MSGS, [])
    entry_b = store.record(MSGS, [])
    # B's response comes back first, then A's — record_usage() is called out
    # of insertion order, which is exactly what a "latest row" heuristic
    # gets wrong.
    store.record_usage({"prompt_tokens": 200, "completion_tokens": 20}, entry_b["id"])
    store.record_usage({"prompt_tokens": 100, "completion_tokens": 10}, entry_a["id"])

    rows = {
        row_id: (prompt, completion)
        for row_id, prompt, completion in store._conn.execute(
            "SELECT id, upstream_prompt, upstream_completion FROM requests"
        )
    }
    assert rows[entry_a["id"]] == (100, 10)
    assert rows[entry_b["id"]] == (200, 20)
    store.close()


def test_record_stores_model(tmp_path):
    store = StatsStore(str(tmp_path / "m.db"))
    entry = store.record(MSGS, [], model="deepseek-v4-flash")
    row = store._conn.execute(
        "SELECT model FROM requests WHERE id=?", (entry["id"],)
    ).fetchone()
    assert row[0] == "deepseek-v4-flash"
    store.close()


def test_record_without_model_stays_null(tmp_path):
    """model is optional — callers that don't pass it (or a request body
    without a model field) get a NULL row, not a crash or a fake default."""
    store = StatsStore(str(tmp_path / "m2.db"))
    entry = store.record(MSGS, [])
    row = store._conn.execute(
        "SELECT model FROM requests WHERE id=?", (entry["id"],)
    ).fetchone()
    assert row[0] is None
    store.close()


def test_migrates_pre_existing_db_missing_model_column(tmp_path):
    """A stats.db written before this column existed must still open and
    accept new writes — CREATE TABLE IF NOT EXISTS doesn't retrofit an
    existing table, so StatsStore has to ALTER TABLE on old files."""
    path = str(tmp_path / "old.db")
    # Build a pre-migration schema by hand (old CREATE TABLE, no model column).
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            tokens_before INTEGER NOT NULL,
            tokens_after INTEGER NOT NULL,
            upstream_prompt INTEGER,
            upstream_completion INTEGER,
            cache_hit INTEGER,
            cache_miss INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO requests (ts, tokens_before, tokens_after) VALUES ('t', 10, 5)"
    )
    conn.commit()
    conn.close()

    store = StatsStore(path)
    # Old row survived the migration, with model implicitly NULL.
    assert store.summary()["request_count"] == 1
    old_row = store._conn.execute("SELECT model FROM requests").fetchone()
    assert old_row[0] is None
    # New writes on the migrated file get the model column normally.
    entry = store.record(MSGS, [], model="deepseek-v4-pro")
    new_row = store._conn.execute(
        "SELECT model FROM requests WHERE id=?", (entry["id"],)
    ).fetchone()
    assert new_row[0] == "deepseek-v4-pro"
    store.close()
