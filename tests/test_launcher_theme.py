from __future__ import annotations

import sys
import types
from pathlib import Path

LAUNCHER_DIR = Path(__file__).resolve().parents[1] / "integrations" / "launcher"
sys.path.insert(0, str(LAUNCHER_DIR))

from launcher_gui import (  # noqa: E402
    PLATFORM_CYRENE,
    LauncherApp,
    _validate_upstream_url,
)
from theme import round_rect_points  # noqa: E402


def test_round_rect_points_stay_inside_requested_bounds() -> None:
    points = round_rect_points(10, 20, 110, 70, 12)

    xs = points[0::2]
    ys = points[1::2]
    assert min(xs) == 10
    assert max(xs) == 110
    assert min(ys) == 20
    assert max(ys) == 70


def test_round_rect_radius_is_clamped_for_small_controls() -> None:
    points = round_rect_points(0, 0, 20, 10, 999)

    # The radius must be at most half the shorter edge, otherwise the
    # smoothed polygon folds over itself on compact buttons.
    assert points[0] == 5
    assert points[1] == 0
    assert points[2] == 15
    assert points[3] == 0


def test_patch_error_survives_deferred_tk_callback(monkeypatch) -> None:
    callbacks = []
    results = []

    class FakeRoot:
        def after(self, _delay, callback) -> None:
            callbacks.append(callback)

    def fail_to_find_settings() -> None:
        raise RuntimeError("settings unavailable")

    monkeypatch.setitem(
        sys.modules,
        "patch_cyrene",
        types.SimpleNamespace(find_model_settings_path=fail_to_find_settings),
    )

    app = object.__new__(LauncherApp)
    app.root = FakeRoot()
    app._patch_done = lambda message, ok: results.append((message, ok))

    app._run_patch(PLATFORM_CYRENE, Path("unused"))
    assert not results

    callbacks[0]()
    assert results == [("改配置失败：settings unavailable", False)]


def test_upstream_url_is_normalized() -> None:
    assert _validate_upstream_url(" https://api.deepseek.com/v1/ ") == (
        "https://api.deepseek.com/v1"
    )


def test_upstream_url_rejects_toml_injection_and_credentials() -> None:
    invalid = [
        'https://safe.example/v1"\nport = 9999',
        "https://user:secret@safe.example/v1",
        "file:///tmp/service",
        "https://safe.example/v1?token=secret",
        "https://safe.example:bad/v1",
        "https://safe.example:99999/v1",
        "https://safe.example/path with spaces",
        "https://safe.example\\evil/v1",
        "https://./v1",
    ]

    for value in invalid:
        try:
            _validate_upstream_url(value)
        except ValueError:
            continue
        raise AssertionError(f"unsafe upstream URL accepted: {value!r}")
