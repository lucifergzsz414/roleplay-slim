from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER_DIR = (
    Path(__file__).resolve().parents[1] / "integrations" / "pet-installer"
)
sys.path.insert(0, str(INSTALLER_DIR))

from installer_theme import (  # noqa: E402
    ACCENT,
    BANNER,
    BG,
    CARD,
    TEXT,
    fit_window_size,
)


def test_installer_theme_matches_the_launcher_brand() -> None:
    assert BG == "#F4F7FB"
    assert CARD == "#FFFFFF"
    assert BANNER == "#0F172A"
    assert TEXT == "#0F172A"
    assert ACCENT == "#2563EB"


def test_installer_window_fits_small_logical_screens() -> None:
    assert fit_window_size(760, 720, 1280, 720) == (760, 662)
    assert fit_window_size(760, 720, 800, 600) == (736, 552)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop layout check")
def test_installer_controls_fit_the_minimum_window() -> None:
    probe = """
import install_gui
install_gui.read_api_key_from_registry = lambda: ""
app = install_gui.InstallerApp()
app.root.geometry("660x560")
app.root.update()
parent = app.log_text.master
assert app.log_text.winfo_x() + app.log_text.winfo_width() <= parent.winfo_width()
assert app.log_scroll.winfo_x() + app.log_scroll.winfo_width() <= parent.winfo_width()
button_bottom = app.install_btn.winfo_rooty() - app.root.winfo_rooty() + app.install_btn.winfo_height()
assert button_bottom <= app.root.winfo_height()
before = app.scroll_body.canvas.yview()
event = type("WheelEvent", (), {"widget": app.log_text, "delta": -120})()
assert app.scroll_body.on_mousewheel(event) == "break"
assert app.scroll_body.canvas.yview() == before
app.root.destroy()
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=INSTALLER_DIR,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_installer_build_bundles_brand_assets(monkeypatch) -> None:
    import build

    captured = {}

    def fake_build(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return build.DIST / build.INSTALLER_EXE_NAME

    monkeypatch.setattr(build, "_build_tk_exe", fake_build)
    result = build.build_installer()

    assert result == build.DIST / build.INSTALLER_EXE_NAME
    assert build.LAUNCHER_ICON.is_file()
    assert all(source.is_file() for source, _destination in build.LAUNCHER_EXTRA_DATA)
    assert captured["kwargs"]["icon"] == build.LAUNCHER_ICON
    assert captured["kwargs"]["extra_data"] == build.LAUNCHER_EXTRA_DATA


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop layout check")
def test_uninstaller_uses_brand_theme_and_fits_small_window() -> None:
    probe = f"""
import uninstall_gui
app = uninstall_gui.UninstallerApp()
app.root.geometry("600x480")
app.root.update()
assert app.root.cget("background") == {BG!r}
assert app._icon_img is not None
parent = app.log_text.master
assert app.log_text.winfo_x() + app.log_text.winfo_width() <= parent.winfo_width()
assert app.log_scroll.winfo_x() + app.log_scroll.winfo_width() <= parent.winfo_width()
assert app.scroll_body.scrollbar.winfo_manager()
before = app.scroll_body.canvas.yview()
event = type("WheelEvent", (), {{"widget": app.scroll_body.canvas, "delta": -120}})()
assert app.scroll_body.on_mousewheel(event) == "break"
app.root.update()
assert app.scroll_body.canvas.yview() != before
button_bottom = app.uninstall_btn.winfo_rooty() - app.root.winfo_rooty() + app.uninstall_btn.winfo_height()
assert button_bottom <= app.root.winfo_height()
app.root.destroy()
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=INSTALLER_DIR,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_uninstaller_build_bundles_brand_assets(monkeypatch) -> None:
    import build

    captured = {}

    def fake_build(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return build.DIST / build.UNINSTALLER_EXE_NAME

    monkeypatch.setattr(build, "_build_tk_exe", fake_build)
    result = build.build_uninstaller()

    assert result == build.DIST / build.UNINSTALLER_EXE_NAME
    assert build.LAUNCHER_ICON.is_file()
    assert all(source.is_file() for source, _destination in build.LAUNCHER_EXTRA_DATA)
    assert captured["kwargs"]["icon"] == build.LAUNCHER_ICON
    assert captured["kwargs"]["extra_data"] == build.LAUNCHER_EXTRA_DATA
