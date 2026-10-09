from __future__ import annotations

from roleplay_slim import stats


def test_fallback_token_estimate_does_not_treat_cjk_as_ascii(monkeypatch) -> None:
    monkeypatch.setattr(stats, "_ENC", None)

    assert stats.estimate_tokens("今天需要复习什么") == 8


def test_fallback_token_estimate_handles_mixed_text(monkeypatch) -> None:
    monkeypatch.setattr(stats, "_ENC", None)

    assert stats.estimate_tokens("test今天") == 3
    assert stats.estimate_tokens("") == 0
