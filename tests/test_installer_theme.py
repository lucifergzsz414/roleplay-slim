from __future__ import annotations

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
def test_installer_controls_fit_the_minimum_window(monkeypatch) -> None:
    import install_gui

    monkeypatch.setattr(install_gui, "read_api_key_from_registry", lambda: "")
    app = install_gui.InstallerApp()
    try:
        app.root.geometry("660x560")
        app.root.update()

        log_parent = app.log_text.master
        assert app.log_text.winfo_x() + app.log_text.winfo_width() <= log_parent.winfo_width()
        assert app.log_scroll.winfo_x() + app.log_scroll.winfo_width() <= log_parent.winfo_width()
        button_bottom = (
            app.install_btn.winfo_rooty()
            - app.root.winfo_rooty()
            + app.install_btn.winfo_height()
        )
        assert button_bottom <= app.root.winfo_height()
    finally:
        app.root.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop event check")
def test_log_wheel_does_not_move_the_outer_page(monkeypatch) -> None:
    import install_gui

    monkeypatch.setattr(install_gui, "read_api_key_from_registry", lambda: "")
    app = install_gui.InstallerApp()
    try:
        app.root.geometry("660x560")
        app.root.update()
        before = app.scroll_body.canvas.yview()
        event = type("WheelEvent", (), {"widget": app.log_text, "delta": -120})()

        assert app.scroll_body.on_mousewheel(event) == "break"
        assert app.scroll_body.canvas.yview() == before
    finally:
        app.root.destroy()


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
    assert captured["kwargs"]["icon"] == build.LAUNCHER_ICON
    assert captured["kwargs"]["extra_data"] == build.LAUNCHER_EXTRA_DATA
