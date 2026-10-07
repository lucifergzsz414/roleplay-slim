from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("name", "relative_path"),
    [
        ("mutsumi_installer", "integrations/pet-installer/installer/install.py"),
        (
            "cyrene_installer",
            "integrations/pet-installer/installer_cyrene/patch_cyrene.py",
        ),
        (
            "bandori_installer",
            "integrations/pet-installer/installer_bandori/patch_bandori.py",
        ),
    ],
)
def test_launchers_delegate_shutdown_to_identity_checked_script(
    name: str, relative_path: str
) -> None:
    module = _load(name, relative_path)
    helper = _load(f"{name}_safe_process", "integrations/pet-installer/safe_process.py")

    assert "stop_proxy.ps1" in module.LAUNCH_PET_BAT
    assert "Get-NetTCPConnection" not in module.LAUNCH_PET_BAT
    assert "taskkill" not in module.LAUNCH_PET_BAT
    assert "$Process.ExecutablePath" in helper.STOP_PROXY_PS1
    assert "$Process.CommandLine" in helper.STOP_PROXY_PS1
    assert "REFUSED_FOREIGN_PROCESS" in helper.STOP_PROXY_PS1


@pytest.mark.parametrize(
    "relative_path",
    [
        "integrations/pet-installer/install_gui.py",
        "integrations/pet-installer/install_bandori_gui.py",
        "integrations/pet-installer/install_cyrene_gui.py",
    ],
)
def test_gui_installers_write_the_identity_checked_stop_script(
    relative_path: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "write_stop_script(config_dir, PROXY_PORT)" in source


def test_uninstaller_does_not_run_an_untrusted_or_missing_stop_script(
    tmp_path: Path,
) -> None:
    helper = _load(
        "safe_process",
        "integrations/pet-installer/safe_process.py",
    )
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, "STOPPED\n", "")

    assert helper.stop_installed_proxy(tmp_path, runner=runner) is False
    assert calls == []

    stop_script = tmp_path / "roleplay-slim-proxy" / "stop_proxy.ps1"
    stop_script.parent.mkdir(parents=True)
    stop_script.write_text("Write-Output 'STOPPED'", encoding="utf-8")

    assert helper.stop_installed_proxy(tmp_path, runner=runner) is False
    assert calls == []


def test_mutsumi_launcher_never_embeds_the_api_key() -> None:
    module = _load("mutsumi_secret_template", "integrations/pet-installer/installer/install.py")

    assert "{api_key}" not in module.LAUNCH_PROXY_BAT
    assert "UPSTREAM_API_KEY=" not in module.LAUNCH_PROXY_BAT
    assert "launch_proxy.ps1" in module.LAUNCH_PROXY_BAT


def test_installer_build_bundles_security_helpers(tmp_path: Path) -> None:
    build = _load("pet_installer_build", "integrations/pet-installer/build.py")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        output = tmp_path / build.INSTALLER_EXE_NAME
        output.write_bytes(b"stub")

    build.DIST = tmp_path
    build.run = fake_run
    build._build_tk_exe(
        build.INSTALLER_SRC,
        "安装器",
        build.INSTALLER_EXE_NAME,
        "installer",
        build.INSTALL_DEPS,
        "install",
    )

    command = commands[0]
    assert ("--paths", str(build.ROOT)) in zip(command, command[1:])
    for helper in ("safe_process", "credential_store"):
        helper_position = command.index(helper)
        assert command[helper_position - 1] == "--hidden-import"


def test_dpapi_protector_passes_secret_only_over_stdin(tmp_path: Path) -> None:
    store = _load(
        "credential_store",
        "integrations/pet-installer/credential_store.py",
    )
    calls = []
    secret = 'sk-test-with-&-and-"-metacharacters'

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        (tmp_path / "api-key.dpapi").write_text("encrypted", encoding="utf-8")
        return subprocess.CompletedProcess(args[0], 0, "", "")

    store.write_dpapi_credential(tmp_path, secret, runner=runner)

    args, kwargs = calls[0]
    assert secret not in " ".join(args[0])
    assert kwargs["input"] == secret
    assert kwargs["shell"] is False
    assert (tmp_path / "launch_proxy.ps1").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI round trip")
def test_dpapi_credential_round_trip_is_encrypted_at_rest(tmp_path: Path) -> None:
    store = _load(
        "credential_store_round_trip",
        "integrations/pet-installer/credential_store.py",
    )
    secret = 'sk-test-&-"-round-trip'

    credential_path, _ = store.write_dpapi_credential(tmp_path, secret)

    assert secret not in credential_path.read_text(encoding="utf-8-sig")
    decrypt_env = os.environ.copy()
    decrypt_env["ROLEPLAY_SLIM_CREDENTIAL_PATH"] = str(credential_path)
    decrypt = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Security;"
            "$e=Get-Content -Raw -LiteralPath "
            "$env:ROLEPLAY_SLIM_CREDENTIAL_PATH;"
            "$p=[Convert]::FromBase64String($e);"
            "$b=[Security.Cryptography.ProtectedData]::Unprotect("
            "$p,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($b))",
        ],
        capture_output=True,
        env=decrypt_env,
        text=True,
        timeout=10,
        check=True,
    )
    assert decrypt.stdout == secret


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell process identity test")
def test_stop_script_refuses_an_unrelated_listener(tmp_path: Path) -> None:
    helper = _load("stop_script_helper", "integrations/pet-installer/safe_process.py")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        script = tmp_path / "stop_proxy.ps1"
        script.write_text(helper.STOP_PROXY_PS1.format(port=port), encoding="utf-8")
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 2
        assert result.stdout.strip() == "REFUSED_FOREIGN_PROCESS"
        assert listener.fileno() >= 0
