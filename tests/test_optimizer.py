"""Tests for roleplay_slim.optimizer (cross-request prefix optimization).

The optimizer's value is finding stability the segmenter can't see within a
single request — a "dynamic" message that is actually byte-identical across
every request, worth hoisting into the cache-stable prefix. These tests pin
the two-rate definition of stability (appearance AND byte-identical, both
required) and the honest limit that only a *leading* run is recommended.
"""
from __future__ import annotations

import pytest

from roleplay_slim.optimizer import analyze

PERSONA = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED = "[Session context]\nFormat rules apply."


def _sample(user_first: str, assistant_replies: list[str]) -> list[dict]:
    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": SHARED},
        {"role": "user", "content": user_first},
    ]
    for r in assistant_replies:
        messages.append({"role": "assistant", "content": r})
    return messages


def _stable_prefix_corpus(n: int = 20) -> list[list[dict]]:
    return [_sample(f"user message {i}", []) for i in range(n)]


def test_finds_stable_prefix_boundary():
    report = analyze(_stable_prefix_corpus())
    assert report.samples == 20
    assert report.recommended_prefix == 2
    assert report.positions[0].stable
    assert report.positions[1].stable
    assert not report.positions[2].stable
    assert report.positions[0].appearance_rate == 1.0
    assert report.positions[0].identical_rate == 1.0
    assert report.positions[0].role == "system"


def test_timestamp_greeting_is_not_stable():
    """100% appearance but content that changes every request has no cache
    value — a timestamp greeting must not count as stable."""
    samples = [_sample(f"现在是 {i} 点了", []) for i in range(20)]
    report = analyze(samples)
    assert report.positions[2].appearance_rate == 1.0
    assert report.positions[2].identical_rate == pytest.approx(1 / 20)
    assert not report.positions[2].stable
    assert report.recommended_prefix == 2


def test_partial_appearance_is_not_stable():
    samples: list[list[dict]] = []
    for i in range(20):
        if i % 2 == 0:
            samples.append(_sample(f"msg {i}", []))  # position 2 present
        else:
            samples.append(
                [
                    {"role": "system", "content": PERSONA},
                    {"role": "system", "content": SHARED},
                ]
            )
    report = analyze(samples)
    assert report.positions[2].appearance_rate == pytest.approx(0.5)
    assert not report.positions[2].stable
    assert report.recommended_prefix == 2


def test_stable_first_user_message_extends_recommended_beyond_segmenter():
    """The key divergence: a message the segmenter calls dynamic (a user
    message) can be byte-identical across every request, so hoisting it
    raises the cache-hit ceiling beyond the segmenter-detected prefix."""
    samples = [_sample("在吗", [f"reply {i}"]) for i in range(30)]
    report = analyze(samples)
    assert report.recommended_prefix == 3
    assert report.prefix_share_by_len[3] > report.prefix_share_by_len[2]
    assert report.prefix_share_by_len[3] > report.current_prefix_share_pct


def test_stable_message_after_varying_does_not_extend():
    """A stable footer sitting after a varying message can't be part of a
    contiguous prefix — the tool is honest and stops at the varying one."""
    samples = [
        [
            {"role": "system", "content": PERSONA},
            {"role": "system", "content": SHARED},
            {"role": "user", "content": f"msg {i}"},
            {"role": "system", "content": "[FORMAT] end with a tag"},
        ]
        for i in range(20)
    ]
    report = analyze(samples)
    assert report.recommended_prefix == 2
    assert not report.positions[2].stable
    assert report.positions[3].stable  # identical every sample, but not leading


def test_thresholds_are_honoured():
    samples = _stable_prefix_corpus()
    strict = analyze(samples)  # default min_identical=0.95
    assert strict.recommended_prefix == 2
    # relax identical-rate enough that the varying user position counts
    loose = analyze(samples, min_identical=0.05, min_appearance=1.0)
    assert loose.recommended_prefix == 3


def test_multimodal_and_tool_content_do_not_crash():
    """content_key (and the preview) must survive non-string content."""
    samples = []
    for i in range(10):
        samples.append(
            [
                {"role": "system", "content": PERSONA},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                    ],
                },
                {"role": "assistant", "content": "A cat. Definitely. Yes."},
            ]
        )
    report = analyze(samples)
    # position 0 stable; the multimodal user content is identical too, so it
    # is stable as well (byte-identical across samples)
    assert report.positions[0].stable
    assert report.positions[1].stable
    assert "multimodal part(s)" in report.positions[1].content_preview


def test_empty_samples_raise():
    with pytest.raises(ValueError, match="at least one sample"):
        analyze([])


def test_invalid_thresholds_raise():
    samples = _stable_prefix_corpus()
    with pytest.raises(ValueError, match="between 0 and 1"):
        analyze(samples, min_appearance=1.5)
