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
    cache_miss INTEGER,
    model TEXT
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
        self._migrate_add_model_column()
        self._conn.commit()

    def _migrate_add_model_column(self) -> None:
        """A stats.db created before this column existed won't have it —
        CREATE TABLE IF NOT EXISTS doesn't retrofit existing tables. Same
        migration story as stats-persistence.md's convo_key note: schema is
        fixed at v1, new columns are a plain ALTER TABLE, applied once."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(requests)")}
        if "model" not in cols:
            self._conn.execute("ALTER TABLE requests ADD COLUMN model TEXT")

    def close(self) -> None:
        self._conn.close()

    @property
    def request_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM requests").fetchone()
        return int(row[0])

    def record(self, before: list[dict], after: list[dict], model: str | None = None) -> dict:
        """Record one request; returns the same entry shape CompressionStats
        produced (plus the row's ``id``) so the proxy's logging stays
        untouched while giving the caller a precise handle for record_usage().

        ``model`` is the upstream model this request targeted (the incoming
        request body's own ``model`` field, passed through as-is) — stored so
        a consumer with per-model pricing can compute real cost per row
        instead of assuming every request used the same tier.
        """
        before_tok = estimate_messages_tokens(before)
        after_tok = estimate_messages_tokens(after)
        cursor = self._conn.execute(
            "INSERT INTO requests (ts, tokens_before, tokens_after, model) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), before_tok, after_tok, model),
        )
        self._conn.commit()
        return {
            "id": cursor.lastrowid,
            "tokens_before": before_tok,
            "tokens_after": after_tok,
            "saved": before_tok - after_tok,
        }

    def record_raw(self, tokens_before: int, tokens_after: int, model: str | None = None) -> dict:
        """Same DB effect as `record()`, but for callers that already have
        their own before/after size numbers and must not have this class
        re-derive them via `estimate_messages_tokens` (which assumes
        OpenAI-shaped `content` — a string or a list of `{"type": "text"}`
        blocks). The Anthropic route computes the equivalent estimated-token
        count from its own block types before calling this method. Routing it
        through `record()` with synthetic OpenAI messages would re-estimate a
        different payload. Column semantics and units are otherwise identical
        to `record()`'s.
        """
        cursor = self._conn.execute(
            "INSERT INTO requests (ts, tokens_before, tokens_after, model) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), tokens_before, tokens_after, model),
        )
        self._conn.commit()
        return {
            "id": cursor.lastrowid,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "saved": tokens_before - tokens_after,
        }

    def record_usage(self, usage: Any, row_id: int) -> dict | None:
        """Back-fill the request identified by ``row_id`` with the
        provider's usage figures.

        row_id is required, not inferred from "the latest row" — under
        concurrent requests, a second request's INSERT can land between this
        one's INSERT and its response coming back, so "latest row" silently
        attributes usage to the wrong request (found via two near-simultaneous
        real requests producing one row with no upstream data and a different
        row with someone else's). The caller gets row_id from record()'s
        return value and threads it through to here.

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
            "cache_hit=?, cache_miss=? WHERE id=?",
            (prompt or 0, completion or 0, hit, miss, row_id),
        )
        self._conn.commit()
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        }

    def summary(self, window: int | None = None) -> dict:
        """The same shape CompressionStats.summary() produced — every field
        the /stats endpoint and its tests rely on.

        window: when given, every figure below is computed over only the
        most recent `window` requests (by insertion order) instead of the
        full lifetime history. A cumulative all-time average dilutes a
        real recent effect (e.g. a burst of duplicate-footer traffic
        pushing compression to 40%+) down toward whatever the very first
        requests looked like, and it makes a real recent cache-hit decline
        indistinguishable from "the number always looked like this" —
        window=N answers "what's happening lately" instead. None (the
        default) is the original all-time behavior, unchanged for every
        existing caller.
        """
        row_filter = ""
        params: tuple = ()
        if window is not None:
            row_filter = "WHERE id IN (SELECT id FROM requests ORDER BY id DESC LIMIT ?)"
            params = (window,)
        count, before, after = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_before), 0), "
            f"COALESCE(SUM(tokens_after), 0) FROM requests {row_filter}",
            params,
        ).fetchone()
        saved = before - after
        pct = (saved / before * 100) if before else 0.0
        return {
            "request_count": int(count),
            "tokens_before_total": before,
            "tokens_after_total": after,
            "tokens_saved_total": saved,
            "savings_pct": round(pct, 2),
            "upstream": self._upstream_summary(window),
        }

    def _upstream_summary(self, window: int | None = None) -> dict | None:
        # The window applies to "the last N requests", not "the last N
        # requests that happen to carry upstream data" — a request with no
        # usage sample (e.g. a non-200 response) still occupies a slot in
        # the window, same as it does in summary()'s request_count.
        if window is not None:
            recent_ids = "id IN (SELECT id FROM requests ORDER BY id DESC LIMIT ?) AND "
            base_params: tuple = (window,)
        else:
            recent_ids = ""
            base_params = ()

        usage_count, prompt_total, completion_total = self._conn.execute(
            "SELECT COUNT(upstream_prompt), COALESCE(SUM(upstream_prompt), 0), "
            "COALESCE(SUM(upstream_completion), 0) FROM requests "
            f"WHERE {recent_ids}upstream_prompt IS NOT NULL",
            base_params,
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
            f"COALESCE(SUM(cache_miss), 0) FROM requests WHERE {recent_ids}cache_hit IS NOT NULL",
            base_params,
        ).fetchone()
        if cache_count:
            counted = hit + miss
            out["cache_hit_tokens_total"] = hit
            out["cache_miss_tokens_total"] = miss
            out["cache_hit_pct"] = round(hit / counted * 100, 2) if counted else 0.0
        return out
