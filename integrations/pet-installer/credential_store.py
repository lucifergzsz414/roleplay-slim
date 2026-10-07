"""Windows DPAPI credential storage for the legacy Mutsumi installer."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

PROTECT_COMMAND = r"""$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$Plain = [Console]::In.ReadToEnd()
if ([String]::IsNullOrWhiteSpace($Plain)) { throw 'API key is empty' }
$Bytes = [System.Text.Encoding]::UTF8.GetBytes($Plain)
$Protected = [System.Security.Cryptography.ProtectedData]::Protect(
    $Bytes,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
$Encrypted = [System.Convert]::ToBase64String($Protected)
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($env:ROLEPLAY_SLIM_CREDENTIAL_PATH, $Encrypted, $Utf8)
"""

LAUNCH_PROXY_PS1 = r"""$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$CredentialPath = Join-Path $PSScriptRoot 'api-key.dpapi'
$Encrypted = Get-Content -LiteralPath $CredentialPath -Raw -Encoding UTF8
$Protected = [System.Convert]::FromBase64String($Encrypted)
$Bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $Protected,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
$env:UPSTREAM_API_KEY = [System.Text.Encoding]::UTF8.GetString($Bytes)

try {
    $ProxyExe = Join-Path $PSScriptRoot 'roleplay-slim-proxy.exe'
    $Config = Join-Path $PSScriptRoot 'config.toml'
    & $ProxyExe --config $Config
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:UPSTREAM_API_KEY -ErrorAction SilentlyContinue
}
"""


def write_dpapi_credential(
    config_dir: Path,
    api_key: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Path, Path]:
    """Encrypt ``api_key`` for the current Windows user without argv exposure."""
    if not api_key or "\x00" in api_key or len(api_key) > 8192:
        raise ValueError("API Key 格式无效")

    config_dir.mkdir(parents=True, exist_ok=True)
    credential_path = config_dir / "api-key.dpapi"
    launcher_path = config_dir / "launch_proxy.ps1"
    child_env = os.environ.copy()
    child_env["ROLEPLAY_SLIM_CREDENTIAL_PATH"] = str(credential_path)
    result = runner(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            PROTECT_COMMAND,
        ],
        input=api_key,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        shell=False,
        env=child_env,
    )
    if result.returncode != 0 or not credential_path.is_file():
        raise RuntimeError("无法使用 Windows DPAPI 保存 API Key")

    launcher_path.write_text(LAUNCH_PROXY_PS1, encoding="utf-8")
    return credential_path, launcher_path
