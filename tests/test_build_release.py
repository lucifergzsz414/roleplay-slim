from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_module():
    path = ROOT / "integrations" / "pet-installer" / "build.py"
    spec = importlib.util.spec_from_file_location("pet_installer_release_build", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_sha256_sidecar_uses_standard_lowercase_format(tmp_path: Path) -> None:
    build = _load_build_module()
    archive = tmp_path / "roleplay-slim启动器.zip"
    archive.write_bytes(b"public beta artifact")

    checksum_path = build.write_sha256_sidecar(archive)

    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum_path == tmp_path / "roleplay-slim启动器.zip.sha256"
    assert checksum_path.read_text(encoding="utf-8") == (
        f"{expected} *roleplay-slim启动器.zip\n"
    )


def test_universal_release_archive_uses_a_stable_ascii_name() -> None:
    build = _load_build_module()

    assert build.LAUNCHER_ZIP_NAME == "roleplay-slim-windows-x64.zip"


def test_tk_build_only_requests_declared_hidden_imports(
    tmp_path: Path, monkeypatch
) -> None:
    build = _load_build_module()
    source = tmp_path / "launcher.py"
    source.write_text("pass\n", encoding="utf-8")
    dependency_dir = tmp_path / "adapter"
    dependency_dir.mkdir()
    dependency = dependency_dir / "install.py"
    dependency.write_text("pass\n", encoding="utf-8")
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs) -> None:
        captured.extend(command)
        (tmp_path / "launcher.exe").write_bytes(b"test executable")

    monkeypatch.setattr(build, "DIST", tmp_path)
    monkeypatch.setattr(build, "run", fake_run)

    result = build._build_tk_exe(
        source,
        "launcher",
        "launcher.exe",
        "launcher",
        [dependency],
        ["install"],
    )

    hidden_imports = [
        captured[index + 1]
        for index, argument in enumerate(captured)
        if argument == "--hidden-import"
    ]
    search_paths = [
        captured[index + 1]
        for index, argument in enumerate(captured)
        if argument == "--paths"
    ]
    assert result == tmp_path / "launcher.exe"
    assert hidden_imports == ["install"]
    assert search_paths == [str(dependency_dir)]
