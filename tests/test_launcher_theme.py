from __future__ import annotations

import sys
import types
from pathlib import Path

import tomllib
from PIL import Image

LAUNCHER_DIR = Path(__file__).resolve().parents[1] / "integrations" / "launcher"
ASSETS_DIR = LAUNCHER_DIR / "assets"
sys.path.insert(0, str(LAUNCHER_DIR))

import launcher_gui  # noqa: E402
from launcher_gui import (  # noqa: E402
    PLATFORM_CYRENE,
    UPSTREAM_PRESETS,
    LauncherApp,
    _calculate_window_size,
    _render_config,
    _use_stacked_layout,
    _validate_upstream_url,
)
from theme import (  # noqa: E402
    ACCENT_SOFT,
    ACCENT_STRONG,
    BG,
    CARD,
    INPUT,
    PANEL,
    SUCCESS_SOFT,
    TEXT,
    round_rect_points,
)


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


def test_launcher_theme_uses_the_reviewed_desktop_palette() -> None:
    assert BG == "#F4F7FB"
    assert CARD == "#FFFFFF"
    assert INPUT == "#F7F9FC"
    assert PANEL == "#F8FAFC"
    assert TEXT == "#0F172A"
    assert ACCENT_STRONG == "#2563EB"
    assert ACCENT_SOFT == "#EFF6FF"
    assert SUCCESS_SOFT == "#ECFDF5"


def test_launcher_icon_assets_cover_header_and_windows_sizes() -> None:
    with Image.open(ASSETS_DIR / "app_icon.png") as icon:
        assert icon.size == (512, 512)
        assert icon.mode == "RGBA"
        assert icon.getchannel("A").getextrema() == (0, 255)

    with Image.open(ASSETS_DIR / "app_header.png") as header:
        assert header.size == (44, 44)

    with Image.open(ASSETS_DIR / "app.ico") as windows_icon:
        assert {
            (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
            (48, 48), (64, 64), (128, 128), (256, 256),
        }.issubset(windows_icon.info["sizes"])


def test_window_icon_keeps_the_multisize_ico_when_available(monkeypatch) -> None:
    calls = []
    branded_image = object()

    class FakeRoot:
        def iconbitmap(self, *, default: str) -> None:
            calls.append(("bitmap", Path(default).name))

        def iconphoto(self, default: bool, image: object) -> None:
            calls.append(("photo", default, image))

    monkeypatch.setattr(launcher_gui, "_bundle_dir", LAUNCHER_DIR)
    monkeypatch.setattr(launcher_gui.tk, "PhotoImage", lambda *, file: branded_image)

    app = object.__new__(LauncherApp)
    app.root = FakeRoot()
    app._icon_img = None
    app._set_window_icon()

    assert app._icon_img is branded_image
    assert ("bitmap", "app.ico") in calls
    assert not any(call[0] == "photo" for call in calls)


def test_window_icon_falls_back_to_the_branded_png(monkeypatch) -> None:
    calls = []
    branded_image = object()

    class FakeRoot:
        def iconbitmap(self, *, default: str) -> None:
            raise launcher_gui.tk.TclError(default)

        def iconphoto(self, default: bool, image: object) -> None:
            calls.append((default, image))

    monkeypatch.setattr(launcher_gui, "_bundle_dir", LAUNCHER_DIR)
    monkeypatch.setattr(launcher_gui.tk, "PhotoImage", lambda *, file: branded_image)

    app = object.__new__(LauncherApp)
    app.root = FakeRoot()
    app._icon_img = None
    app._set_window_icon()

    assert calls == [(True, branded_image)]


def test_window_size_stays_inside_small_logical_screens() -> None:
    assert _calculate_window_size(1112, 774, 1280, 720) == (1112, 662)
    assert _calculate_window_size(1112, 774, 1024, 768) == (942, 706)


def test_layout_stacks_from_the_actual_available_width() -> None:
    assert _use_stacked_layout(920)
    assert _use_stacked_layout(1024)
    assert not _use_stacked_layout(1112)
    assert not _use_stacked_layout(1707)


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


def test_launcher_provider_presets_include_bailian_model() -> None:
    assert UPSTREAM_PRESETS["阿里云百炼"]["base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert UPSTREAM_PRESETS["阿里云百炼"]["model"] == "qwen3.6-plus"


def test_render_config_writes_explicit_model_override() -> None:
    rendered = _render_config(
        upstream="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        port=8795,
        db_path=Path(r"C:\Users\Example\stats.db"),
    )

    parsed = tomllib.loads(rendered)
    assert parsed["proxy"]["upstream_base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert parsed["proxy"]["upstream_model"] == "qwen3.6-plus"
    assert parsed["stats"]["db_path"] == r"C:\Users\Example\stats.db"


def test_render_config_omits_blank_model_to_preserve_client_request() -> None:
    rendered = _render_config(
        upstream="https://api.deepseek.com/v1",
        model="  ",
        port=8795,
        db_path=Path("stats.db"),
    )

    parsed = tomllib.loads(rendered)
    assert "upstream_model" not in parsed["proxy"]
