from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import tomllib
from PIL import Image

LAUNCHER_DIR = Path(__file__).resolve().parents[1] / "integrations" / "launcher"
ASSETS_DIR = LAUNCHER_DIR / "assets"
sys.path.insert(0, str(LAUNCHER_DIR))

import launcher_gui  # noqa: E402
from launcher_gui import (  # noqa: E402
    PLATFORM_UNIVERSAL,
    PLATFORMS,
    UPSTREAM_PRESETS,
    LauncherApp,
    _calculate_window_size,
    _render_config,
    _stop_spawned_process,
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


def test_upstream_url_is_normalized() -> None:
    assert _validate_upstream_url(" https://api.deepseek.com/v1/ ") == (
        "https://api.deepseek.com/v1"
    )


def test_upstream_url_allows_http_only_for_loopback() -> None:
    assert _validate_upstream_url("http://127.0.0.1:8000/v1") == (
        "http://127.0.0.1:8000/v1"
    )
    assert _validate_upstream_url("http://localhost:8000/v1") == (
        "http://localhost:8000/v1"
    )

    with pytest.raises(ValueError, match="HTTPS"):
        _validate_upstream_url("http://api.example.com/v1")


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


def test_universal_release_exposes_only_generic_connection_mode() -> None:
    assert PLATFORMS == [PLATFORM_UNIVERSAL]


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


def test_windows_stop_terminates_the_owned_process_tree() -> None:
    calls = []

    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False
        waited = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            self.waited = True
            assert timeout == 5
            return 0

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(returncode=0)

    process = FakeProcess()
    _stop_spawned_process(process, runner=fake_run, platform="nt")

    assert calls[0][0] == ["taskkill", "/PID", "4242", "/T", "/F"]
    assert calls[0][1]["check"] is False
    assert process.waited
    assert not process.terminated
    assert not process.killed


def test_windows_stop_falls_back_when_taskkill_cannot_start() -> None:
    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            return 0

    def unavailable_runner(*args, **kwargs):
        raise FileNotFoundError("taskkill unavailable")

    process = FakeProcess()
    _stop_spawned_process(process, runner=unavailable_runner, platform="win32")

    assert process.terminated
    assert not process.killed


def test_windows_stop_falls_back_when_taskkill_fails() -> None:
    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            return 0

    process = FakeProcess()
    _stop_spawned_process(
        process,
        runner=lambda *args, **kwargs: types.SimpleNamespace(returncode=1),
        platform="nt",
    )

    assert process.terminated
    assert not process.killed


def test_windows_stop_falls_back_when_taskkill_exit_is_not_observed() -> None:
    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False
        waits = 0

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            self.waits += 1
            if self.waits == 1:
                raise TimeoutError
            return 0

    process = FakeProcess()
    _stop_spawned_process(
        process,
        runner=lambda *args, **kwargs: types.SimpleNamespace(returncode=0),
        platform="win32",
    )

    assert process.terminated
    assert not process.killed
    assert process.waits == 2


def test_non_windows_stop_uses_process_api_only() -> None:
    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            return 0

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("runner must not be used")

    process = FakeProcess()
    _stop_spawned_process(process, runner=unexpected_runner, platform="linux")

    assert process.terminated
    assert not process.killed


def test_stop_escalates_to_kill_after_wait_failure() -> None:
    class FakeProcess:
        pid = 4242
        terminated = False
        killed = False
        waits = 0

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            self.waits += 1
            if self.waits == 1:
                raise TimeoutError
            return 0

    process = FakeProcess()
    _stop_spawned_process(process, platform="linux")

    assert process.terminated
    assert process.killed
    assert process.waits == 2


def test_stop_does_nothing_for_exited_process() -> None:
    class ExitedProcess:
        pid = 4242

        def poll(self):
            return 0

        def terminate(self) -> None:
            raise AssertionError("terminate must not be used")

        def kill(self) -> None:
            raise AssertionError("kill must not be used")

        def wait(self, timeout: int) -> int:
            raise AssertionError("wait must not be used")

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("runner must not be used")

    _stop_spawned_process(
        ExitedProcess(), runner=unexpected_runner, platform="win32"
    )
