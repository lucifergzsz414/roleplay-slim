"""SQLite-backed stats accumulator for the proxy.

Replaces the in-memory CompressionStats as the proxy's source of truth for
``/stats`` so the numbers survive a restart. One row per request; the
``tokens_*`` columns are local estimates written at record time, and the
``upstream_*`` columns are the provider's own accounting back-filled by
``record_usage`` when a non-streaming response carries a ``usage`` block
(streaming responses usually don't, so those rows keep NULL upstream
columns).

With ``:memory:`` as the path it is a drop-in in-process accumulator with
identical aggregation semantics — ``persist=false`` in the config gives the
old behavior, zero file created, byte-compatible ``/stats`` output.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from ..stats import _coerce_int, estimate_messages_tokens

logger = logging.getLogger("roleplay_slim")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    tokens_before INTEGER NOT NULL,
    tokens_after INTEGER NOT NULL,
    upstream_prompt INTEGER,
    upstream_completion INTEGER,
    cache_hit INTEGER,
    cache_miss INTEGER
)
"""


class StatsStore:
    def __init__(self, path: str = "stats.db") -> None:
        # A stats database is telemetry — it must never take down the live
        # proxy. If the path can't be opened (unwritable directory, which a
        # systemd service's CWD often is for a relative default), degrade to
        # in-memory with a warning rather than crash on startup.
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False)
        except sqlite3.Error:
            logger.warning(
                "could not open stats database %r — falling back to in-memory "
                "stats; /stats will not survive a restart. Set an absolute, "
                "writable [stats] db_path to persist.",
                path,
            )
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def request_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM requests").fetchone()
        return int(row[0])

    def record(self, before: list[dict], after: list[dict]) -> dict:
        """Record one request; returns the same entry shape CompressionStats
        produced so the proxy's logging stays untouched."""
        before_tok = estimate_messages_tokens(before)
        after_tok = estimate_messages_tokens(after)
        self._conn.execute(
            "INSERT INTO requests (ts, tokens_before, tokens_after) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), before_tok, after_tok),
        )
        self._conn.commit()
        return {
            "tokens_before": before_tok,
            "tokens_after": after_tok,
            "saved": before_tok - after_tok,
        }

    def record_usage(self, usage: Any) -> dict | None:
        """Back-fill the most recent row with the provider's usage figures.

        Mirrors CompressionStats.record_usage's tolerance: a wrong number is
        worse than a missing one here (these figures back the cache claim),
        so unparseable fields are dropped rather than guessed at.
        """
        if not isinstance(usage, dict):
            return None
        prompt = _coerce_int(usage.get("prompt_tokens"))
        completion = _coerce_int(usage.get("completion_tokens"))
        if prompt is None and completion is None:
            return None
        hit = _coerce_int(usage.get("prompt_cache_hit_tokens"))
        miss = _coerce_int(usage.get("prompt_cache_miss_tokens"))
        self._conn.execute(
            "UPDATE requests SET upstream_prompt=?, upstream_completion=?, "
            "cache_hit=?, cache_miss=? WHERE id=(SELECT MAX(id) FROM requests)",
            (prompt or 0, completion or 0, hit, miss),
        )
        self._conn.commit()
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        }

    def summary(self) -> dict:
        """The same shape CompressionStats.summary() produced — every field
        the /stats endpoint and its tests rely on."""
        count, before, after = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_before), 0), "
            "COALESCE(SUM(tokens_after), 0) FROM requests"
        ).fetchone()
        saved = before - after
        pct = (saved / before * 100) if before else 0.0
        return {
            "request_count": int(count),
            "tokens_before_total": before,
            "tokens_after_total": after,
            "tokens_saved_total": saved,
            "savings_pct": round(pct, 2),
            "upstream": self._upstream_summary(),
        }

    def _upstream_summary(self) -> dict | None:
        usage_count, prompt_total, completion_total = self._conn.execute(
            "SELECT COUNT(upstream_prompt), COALESCE(SUM(upstream_prompt), 0), "
            "COALESCE(SUM(upstream_completion), 0) FROM requests "
            "WHERE upstream_prompt IS NOT NULL"
        ).fetchone()
        if not usage_count:
            return None
        out: dict = {
            "usage_sample_count": int(usage_count),
            "prompt_tokens_total": prompt_total,
            "completion_tokens_total": completion_total,
            "cache_hit_tokens_total": None,
            "cache_miss_tokens_total": None,
            "cache_hit_pct": None,
        }
        cache_count, hit, miss = self._conn.execute(
            "SELECT COUNT(cache_hit), COALESCE(SUM(cache_hit), 0), "
            "COALESCE(SUM(cache_miss), 0) FROM requests WHERE cache_hit IS NOT NULL"
        ).fetchone()
        if cache_count:
            counted = hit + miss
            out["cache_hit_tokens_total"] = hit
            out["cache_miss_tokens_total"] = miss
            out["cache_hit_pct"] = round(hit / counted * 100, 2) if counted else 0.0
        return out
