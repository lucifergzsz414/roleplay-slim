"""Build standalone Windows executables for distribution.

This toolchain is private pet-distribution tooling, not part of the
published roleplay-slim library or package — it lives under
integrations/pet-installer/, separate from src/roleplay_slim.

Produces two self-contained .exe files:
    1. 安装器.exe          — GUI installer (no Python required)
    2. roleplay-slim-proxy.exe — the compression proxy itself

Usage (run from this directory, integrations/pet-installer/):
    python build.py                    # build both
    python build.py --installer-only   # only the installer
    python build.py --proxy-only       # only the proxy
    python build.py --zip              # build + package into dist/ zip
    python build.py --bandori-only     # BandoriPet installer + uninstaller
    python build.py --bandori-zip      # + package into dist/ zip
    python build.py --cyrene-only      # Cyrene-Agent installer + uninstaller
    python build.py --cyrene-zip       # + package into dist/ zip
    python build.py --launcher-only    # universal launcher (ordinary-user facing)
    python build.py --launcher-zip     # + package launcher + proxy into dist/ zip

Requirements:
    pip install pyinstaller
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# This toolchain lives at <repo>/integrations/pet-installer/ — two levels
# below the repo root, which is where the actual roleplay_slim package
# (the thing being frozen into the proxy exe) lives.
REPO_ROOT = ROOT.parent.parent
DIST = ROOT / "dist"
INSTALLER_SRC = ROOT / "install_gui.py"
UNINSTALLER_SRC = ROOT / "uninstall_gui.py"
INSTALLER_DIR = ROOT / "installer"
INSTALL_DEPS = [INSTALLER_DIR / "install.py"]
PROXY_ENTRY = ROOT / "proxy_entry.py"
PROXY_PACKAGE = REPO_ROOT / "src" / "roleplay_slim"

INSTALLER_EXE_NAME = "安装器.exe"
UNINSTALLER_EXE_NAME = "卸载还原器.exe"
PROXY_EXE_NAME = "roleplay-slim-proxy.exe"
ZIP_NAME = "若叶睦桌宠-上下文优化代理.zip"
README_SRC = ROOT / "使用说明.txt"

# BandoriPet (Electron) — separate installer/uninstaller, shares the same
# proxy exe as Mutsumi, just a different default port (8792 vs 8791).
BANDORI_INSTALLER_SRC = ROOT / "install_bandori_gui.py"
BANDORI_UNINSTALLER_SRC = ROOT / "uninstall_bandori_gui.py"
BANDORI_INSTALLER_DIR = ROOT / "installer_bandori"
BANDORI_DEPS = [BANDORI_INSTALLER_DIR / "patch_bandori.py"]

BANDORI_INSTALLER_EXE_NAME = "BandoriPet安装器.exe"
BANDORI_UNINSTALLER_EXE_NAME = "BandoriPet卸载还原器.exe"
BANDORI_ZIP_NAME = "邦多利桌宠-上下文优化代理.zip"
BANDORI_README_SRC = ROOT / "使用说明_BandoriPet.txt"

# Cyrene-Agent (Electron) — same proxy exe, own port (8793) and own
# installer since it patches a JSON settings file instead of index.html.
CYRENE_INSTALLER_SRC = ROOT / "install_cyrene_gui.py"
CYRENE_UNINSTALLER_SRC = ROOT / "uninstall_cyrene_gui.py"
CYRENE_INSTALLER_DIR = ROOT / "installer_cyrene"
CYRENE_DEPS = [CYRENE_INSTALLER_DIR / "patch_cyrene.py"]

CYRENE_INSTALLER_EXE_NAME = "Cyrene安装器.exe"
CYRENE_UNINSTALLER_EXE_NAME = "Cyrene卸载还原器.exe"
CYRENE_ZIP_NAME = "Cyrene桌宠-上下文优化代理.zip"
CYRENE_README_SRC = ROOT / "使用说明_Cyrene.txt"

# Universal launcher — unlike the three installers above (run once, patch one
# app, exit), this one stays open, runs the proxy itself, and shows live
# savings. It's the artifact aimed at ordinary users rather than at people
# who already know what a proxy is, so it bundles all three patch modules
# (its "one-click" path can target any adapted app) and defaults to a
# copy-the-URL mode that works with anything OpenAI-compatible.
LAUNCHER_SRC = ROOT.parent / "launcher" / "launcher_gui.py"
LAUNCHER_DEPS = [
    INSTALLER_DIR / "install.py",
    BANDORI_INSTALLER_DIR / "patch_bandori.py",
    CYRENE_INSTALLER_DIR / "patch_cyrene.py",
]
LAUNCHER_HIDDEN_IMPORTS = ["install", "patch_bandori", "patch_cyrene"]
SECURITY_HELPER_IMPORTS = ["safe_process", "credential_store"]
LAUNCHER_EXE_NAME = "roleplay-slim启动器.exe"
LAUNCHER_ZIP_NAME = "roleplay-slim启动器.zip"
LAUNCHER_README_SRC = ROOT / "使用说明_启动器.txt"
LAUNCHER_ASSETS = ROOT.parent / "launcher" / "assets"
LAUNCHER_ICON = LAUNCHER_ASSETS / "app.ico"
# Tk reads both resources at runtime.  The ICO keeps Windows titlebar/taskbar
# rendering crisp at every size; the PNG is used inside the branded header.
LAUNCHER_EXTRA_DATA = [
    (LAUNCHER_ASSETS / "app.ico", "assets"),
    (LAUNCHER_ASSETS / "app_header.png", "assets"),
]


def pyinstaller_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, text=True, check=True,
        )
        return True
    except Exception:
        return False


def step(msg: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {msg}")
    print(f"{'=' * 55}")


def run(cmd: list[str], **kwargs) -> None:
    """Run a command, streaming output."""
    print(f"  -> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT), **kwargs)
    if result.returncode != 0:
        print(f"  [X] Failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def _build_tk_exe(
    source: Path, name: str, exe_name: str, workdir_suffix: str,
    deps: list[Path], hidden_import: str | list[str],
    icon: Path | None = None,
    extra_data: list[tuple[Path, str]] | None = None,
) -> Path:
    """Shared PyInstaller invocation for the tkinter GUIs (installer,
    uninstaller, and their BandoriPet counterparts) — all of them bundle a
    single patch-logic module the same way.

    `icon` sets the .exe's own icon (what Explorer and the taskbar show).
    `extra_data` bundles further files as (source, destination-subdir) —
    the launcher needs its in-window PNG this way, since a frozen app can't
    read anything that wasn't shipped inside it."""
    step(f"Building {exe_name}")
    DIST.mkdir(exist_ok=True)

    workpath = DIST / f"_build_{workdir_suffix}"
    specpath = workpath

    # Clean prior build artifacts
    if workpath.exists():
        shutil.rmtree(workpath)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", name,
        "--distpath", str(DIST),
        "--workpath", str(workpath),
        "--specpath", str(specpath),
        "--noconsole",
        "--paths", str(ROOT),
    ]

    # Ensure the patch-logic module is included (runtime sys.path insertion
    # confuses the PyInstaller import analyser)
    for dep in deps:
        cmd.extend(["--add-data", f"{dep}{os.pathsep}{dep.parent.name}"])
        # Also put the patch module's own directory on PyInstaller's build
        # search path. Without this, `--hidden-import <patch>` below can't
        # resolve the module, so PyInstaller never analyses it and never
        # bundles its real dependencies — found via Cyrene's installer
        # crashing with `No module named 'json'` on another machine: the
        # entry script doesn't import json, only patch_cyrene.py does, so
        # json was left out of the frozen bundle entirely.
        cmd.extend(["--paths", str(dep.parent)])

    # Hidden imports that PyInstaller might miss. The launcher needs several
    # (it can patch any of the three adapted apps), the single-app installers
    # need exactly one — both spellings accepted so neither has to carry a
    # one-element list.
    requested_hidden_imports = (
        [hidden_import] if isinstance(hidden_import, str) else hidden_import
    )
    for _hidden in [*requested_hidden_imports, *SECURITY_HELPER_IMPORTS]:
        cmd.extend(["--hidden-import", _hidden])

    if icon is not None and icon.is_file():
        cmd.extend(["--icon", str(icon)])
    for src, dest_dir in (extra_data or []):
        if src.is_file():
            cmd.extend(["--add-data", f"{src}{os.pathsep}{dest_dir}"])

    cmd.append(str(source))

    run(cmd)

    # Move the .exe up if PyInstaller put it in a subdirectory
    built = DIST / exe_name
    if not built.is_file():
        alt = DIST / name / exe_name
        if alt.is_file():
            shutil.move(str(alt), str(built))

    if built.is_file():
        size_mb = built.stat().st_size / (1024 * 1024)
        print(f"  [OK] {exe_name} ({size_mb:.1f} MB)")
    else:
        print(f"  [X] Output not found: {built}")
        sys.exit(1)

    return built


def build_installer() -> Path:
    """Build the Mutsumi GUI installer as a single-file .exe."""
    return _build_tk_exe(INSTALLER_SRC, "安装器", INSTALLER_EXE_NAME, "installer", INSTALL_DEPS, "install")


def build_uninstaller() -> Path:
    """Build the Mutsumi GUI uninstaller as a single-file .exe."""
    return _build_tk_exe(UNINSTALLER_SRC, "卸载还原器", UNINSTALLER_EXE_NAME, "uninstaller", INSTALL_DEPS, "install")


def build_bandori_installer() -> Path:
    """Build the BandoriPet GUI installer as a single-file .exe."""
    return _build_tk_exe(
        BANDORI_INSTALLER_SRC, "BandoriPet安装器", BANDORI_INSTALLER_EXE_NAME,
        "bandori_installer", BANDORI_DEPS, "patch_bandori",
    )


def build_bandori_uninstaller() -> Path:
    """Build the BandoriPet GUI uninstaller as a single-file .exe."""
    return _build_tk_exe(
        BANDORI_UNINSTALLER_SRC, "BandoriPet卸载还原器", BANDORI_UNINSTALLER_EXE_NAME,
        "bandori_uninstaller", BANDORI_DEPS, "patch_bandori",
    )


def build_cyrene_installer() -> Path:
    """Build the Cyrene-Agent GUI installer as a single-file .exe."""
    return _build_tk_exe(
        CYRENE_INSTALLER_SRC, "Cyrene安装器", CYRENE_INSTALLER_EXE_NAME,
        "cyrene_installer", CYRENE_DEPS, "patch_cyrene",
    )


def build_cyrene_uninstaller() -> Path:
    """Build the Cyrene-Agent GUI uninstaller as a single-file .exe."""
    return _build_tk_exe(
        CYRENE_UNINSTALLER_SRC, "Cyrene卸载还原器", CYRENE_UNINSTALLER_EXE_NAME,
        "cyrene_uninstaller", CYRENE_DEPS, "patch_cyrene",
    )


def build_launcher() -> Path:
    """Build the universal launcher as a single-file .exe."""
    return _build_tk_exe(
        LAUNCHER_SRC, "roleplay-slim启动器", LAUNCHER_EXE_NAME,
        "launcher", LAUNCHER_DEPS, LAUNCHER_HIDDEN_IMPORTS,
        icon=LAUNCHER_ICON, extra_data=LAUNCHER_EXTRA_DATA,
    )


def build_proxy() -> Path:
    """Build the compression proxy as a single-file .exe."""
    step("Building roleplay-slim-proxy.exe")
    DIST.mkdir(exist_ok=True)

    workpath = DIST / "_build_proxy"
    specpath = DIST / "_build_proxy"

    if workpath.exists():
        shutil.rmtree(workpath)

    # Exclude heavy packages that PyInstaller pulls in from the dev
    # environment but that the proxy never touches.
    _proxy_exclude = [
        "matplotlib", "PySide6", "shiboken6",
        "IPython", "jupyter_client", "jupyter_core", "nbformat", "traitlets",
        "numpy", "PIL", "lxml", "zmq", "wmi",
        "jedi", "parso", "pygments",
        "cryptography", "bcrypt", "nacl",
        "rich", "wcwidth", "prompt_toolkit", "debugpy",
        "pytest", "hypothesis",
    ]
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "roleplay-slim-proxy",
        "--distpath", str(DIST),
        "--workpath", str(workpath),
        "--specpath", str(specpath),
        "--console",
        # Make the entire roleplay_slim package available
        "--paths", str(PROXY_PACKAGE.parent),
        # Dependencies PyInstaller might not auto-detect
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "fastapi",
        "--hidden-import", "httpx",
        "--collect-submodules", "roleplay_slim",
        *[f"--exclude-module={m}" for m in _proxy_exclude],
        str(PROXY_ENTRY),
    ]

    run(cmd)

    built = DIST / PROXY_EXE_NAME
    if not built.is_file():
        alt = DIST / "roleplay-slim-proxy" / f"{PROXY_EXE_NAME}"
        if alt.is_file():
            shutil.move(str(alt), str(built))

    if built.is_file():
        size_mb = built.stat().st_size / (1024 * 1024)
        print(f"  [OK] {PROXY_EXE_NAME} ({size_mb:.1f} MB)")
    else:
        print(f"  [X] Output not found: {built}")
        sys.exit(1)

    return built


def package_zip(zip_name: str, files: list[Path], readme: Path | None = None) -> Path:
    """Bundle the given files (+ optional readme) into a distributable zip."""
    step(f"Packaging {zip_name}")
    DIST.mkdir(exist_ok=True)

    zip_path = DIST / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.name)
            print(f"  + {f.name}")
        if readme and readme.is_file():
            zf.write(readme, readme.name)
            print(f"  + {readme.name}")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] {zip_name} ({size_mb:.1f} MB)")
    print(f"  -> {zip_path}")
    return zip_path


def main() -> None:
    installer_only = "--installer-only" in sys.argv
    uninstaller_only = "--uninstaller-only" in sys.argv
    proxy_only = "--proxy-only" in sys.argv
    do_zip = "--zip" in sys.argv

    bandori_installer_only = "--bandori-installer-only" in sys.argv
    bandori_uninstaller_only = "--bandori-uninstaller-only" in sys.argv
    bandori_only = "--bandori-only" in sys.argv  # both bandori exes, no proxy rebuild
    do_bandori_zip = "--bandori-zip" in sys.argv

    cyrene_installer_only = "--cyrene-installer-only" in sys.argv
    cyrene_uninstaller_only = "--cyrene-uninstaller-only" in sys.argv
    cyrene_only = "--cyrene-only" in sys.argv  # both cyrene exes, no proxy rebuild
    do_cyrene_zip = "--cyrene-zip" in sys.argv

    launcher_only = "--launcher-only" in sys.argv
    do_launcher_zip = "--launcher-zip" in sys.argv

    any_only = (
        installer_only or uninstaller_only or proxy_only
        or bandori_installer_only or bandori_uninstaller_only or bandori_only
        or cyrene_installer_only or cyrene_uninstaller_only or cyrene_only
        or launcher_only
    )
    # Only rebuild the default Mutsumi trio if a build flag is explicitly
    # given, AND zip-only doesn't imply rebuild.
    want_build = any_only or (
        not do_zip and not do_bandori_zip and not do_cyrene_zip and not do_launcher_zip
    )
    both = want_build and not any_only

    if not pyinstaller_available():
        print("[X] PyInstaller not found. Installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )

    installer = None
    uninstaller = None
    proxy = None
    bandori_installer = None
    bandori_uninstaller = None
    cyrene_installer = None
    cyrene_uninstaller = None
    launcher = None

    if installer_only or both:
        installer = build_installer()

    if uninstaller_only or both:
        uninstaller = build_uninstaller()

    if proxy_only or both:
        proxy = build_proxy()

    if bandori_installer_only or bandori_only:
        bandori_installer = build_bandori_installer()

    if bandori_uninstaller_only or bandori_only:
        bandori_uninstaller = build_bandori_uninstaller()

    if cyrene_installer_only or cyrene_only:
        cyrene_installer = build_cyrene_installer()

    if cyrene_uninstaller_only or cyrene_only:
        cyrene_uninstaller = build_cyrene_uninstaller()

    if launcher_only:
        launcher = build_launcher()

    if do_launcher_zip:
        if not launcher:
            launcher = DIST / LAUNCHER_EXE_NAME
        if not proxy:
            proxy = DIST / PROXY_EXE_NAME
        if not launcher.is_file() or not proxy.is_file():
            print("[X] Launcher + proxy exe must both exist to package zip. Build them first "
                  "(python build.py --launcher-only --proxy-only).")
            sys.exit(1)
        if not LAUNCHER_README_SRC.is_file():
            print(f"[X] {LAUNCHER_README_SRC.name} is missing — write it before packaging.")
            sys.exit(1)
        package_zip(LAUNCHER_ZIP_NAME, [launcher, proxy], LAUNCHER_README_SRC)

    if do_zip:
        if not installer:
            installer = DIST / INSTALLER_EXE_NAME
        if not uninstaller:
            uninstaller = DIST / UNINSTALLER_EXE_NAME
        if not proxy:
            proxy = DIST / PROXY_EXE_NAME
        if not installer.is_file() or not uninstaller.is_file() or not proxy.is_file():
            print("[X] All three .exe files must exist to package zip. Build them first.")
            sys.exit(1)
        if not README_SRC.is_file():
            print(f"[X] {README_SRC.name} is missing — it is private pet-distribution content, "
                  "not part of the open-source repo.")
            print("    Restore it from git history "
                  "(git show 8cb03b3^:使用说明.txt > 使用说明.txt) or provide your private copy.")
            sys.exit(1)
        package_zip(ZIP_NAME, [installer, uninstaller, proxy], README_SRC)

    if do_bandori_zip:
        if not bandori_installer:
            bandori_installer = DIST / BANDORI_INSTALLER_EXE_NAME
        if not bandori_uninstaller:
            bandori_uninstaller = DIST / BANDORI_UNINSTALLER_EXE_NAME
        if not proxy:
            proxy = DIST / PROXY_EXE_NAME
        if not bandori_installer.is_file() or not bandori_uninstaller.is_file() or not proxy.is_file():
            print("[X] BandoriPet installer/uninstaller + proxy exe must all exist to package zip.")
            sys.exit(1)
        if not BANDORI_README_SRC.is_file():
            print(f"[X] {BANDORI_README_SRC.name} is missing — private pet-distribution content, "
                  "not part of the open-source repo.")
            print("    Restore it from git history or provide your private copy.")
            sys.exit(1)
        package_zip(BANDORI_ZIP_NAME, [bandori_installer, bandori_uninstaller, proxy], BANDORI_README_SRC)

    if do_cyrene_zip:
        if not cyrene_installer:
            cyrene_installer = DIST / CYRENE_INSTALLER_EXE_NAME
        if not cyrene_uninstaller:
            cyrene_uninstaller = DIST / CYRENE_UNINSTALLER_EXE_NAME
        if not proxy:
            proxy = DIST / PROXY_EXE_NAME
        if not cyrene_installer.is_file() or not cyrene_uninstaller.is_file() or not proxy.is_file():
            print("[X] Cyrene installer/uninstaller + proxy exe must all exist to package zip.")
            sys.exit(1)
        if not CYRENE_README_SRC.is_file():
            print(f"[X] {CYRENE_README_SRC.name} is missing — private pet-distribution content, "
                  "not part of the open-source repo.")
            print("    Provide your own copy before packaging the zip.")
            sys.exit(1)
        package_zip(CYRENE_ZIP_NAME, [cyrene_installer, cyrene_uninstaller, proxy], CYRENE_README_SRC)

    print(f"\n{'=' * 55}")
    print("  Done!")
    if installer:
        print(f"  安装器:      {installer}")
    if uninstaller:
        print(f"  卸载还原器:   {uninstaller}")
    if proxy:
        print(f"  代理:        {proxy}")
    if bandori_installer:
        print(f"  Bandori安装器: {bandori_installer}")
    if bandori_uninstaller:
        print(f"  Bandori卸载器: {bandori_uninstaller}")
    if launcher:
        print(f"  启动器:      {launcher}")
    if cyrene_installer:
        print(f"  Cyrene安装器:  {cyrene_installer}")
    if cyrene_uninstaller:
        print(f"  Cyrene卸载器:  {cyrene_uninstaller}")
    if do_zip:
        print(f"  分发包:      {DIST / ZIP_NAME}")
    if do_bandori_zip:
        print(f"  Bandori分发包: {DIST / BANDORI_ZIP_NAME}")
    if do_cyrene_zip:
        print(f"  Cyrene分发包:  {DIST / CYRENE_ZIP_NAME}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
