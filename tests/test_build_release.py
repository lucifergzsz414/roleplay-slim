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
