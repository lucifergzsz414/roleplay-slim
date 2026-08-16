"""CI gate: assert the built sdist contains only files the pyproject.toml
sdist allowlist declares — nothing more, nothing less.

History: 0.3.1 shipped unrelated local scripts because hatchling sweeps
everything .gitignore doesn't exclude into the sdist. The fix was an
explicit *allowlist* in ``[tool.hatch.build.targets.sdist] include``. This
script makes a regression impossible: it builds the sdist and fails if

  * any file in the archive falls outside the allowlist (the 0.3.1 bug), or
  * any allowlisted path that has content on disk shipped nothing (a
    typo'd/empty pattern silently disabling a whole tree — the same class
    of bug in the other direction).

Uses ``hatch build`` rather than ``python -m build``: the repo-root
``build.py`` (a tracked PyInstaller script) shadows the build module name —
see gotchas.md.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent

# hatchling always writes a PKG-INFO into the sdist root regardless of the
# allowlist; it is generated metadata, not a shipped source file.
ALLOWED_METADATA = {"PKG-INFO"}


def _load_include_patterns(repo: pathlib.Path) -> list[str]:
    with open(repo / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


def _backup_existing_tarballs(
    dist_dir: pathlib.Path, tmpdir: pathlib.Path
) -> dict[pathlib.Path, pathlib.Path]:
    """Copy any existing ``roleplay_slim-*.tar.gz`` out of ``dist/`` so a
    build that overwrites them can be rolled back. ``hatch build`` has no
    output-directory flag — it always writes into the project's ``dist/``,
    and this gate must leave ``dist/`` exactly as it found it (that
    directory also holds the end-user distribution ZIPs and release
    artifacts, never to be disturbed)."""
    backups: dict[pathlib.Path, pathlib.Path] = {}
    for p in dist_dir.glob("roleplay_slim-*.tar.gz"):
        bak = tmpdir / (p.name + ".bak")
        shutil.copy2(p, bak)
        backups[p] = bak
    return backups


def _restore_or_cleanup(
    dist_dir: pathlib.Path, backups: dict[pathlib.Path, pathlib.Path]
) -> None:
    """Put ``dist/`` back the way it was: restore any tarball we overwrote,
    delete any tarball we created that wasn't there before."""
    for p in dist_dir.glob("roleplay_slim-*.tar.gz"):
        if p in backups:
            shutil.copy2(backups[p], p)
        else:
            p.unlink(missing_ok=True)


def _build_sdist(repo: pathlib.Path) -> pathlib.Path:
    """Build the sdist via ``hatch build -t sdist`` and return its path.

    Artifacts land in the project's ``dist/``; the caller is responsible for
    restoring ``dist/`` afterwards (see _backup_existing_tarballs /
    _restore_or_cleanup).
    """
    proc = subprocess.run(
        ["hatch", "build", "-t", "sdist"],
        cwd=repo,
        capture_output=True,
        text=True,
        # hatch's output includes non-ASCII characters that fail to decode
        # under the system codepage on Windows; read it as UTF-8 explicitly.
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "hatch build failed:\n" + (proc.stderr or proc.stdout or "(no output)")
        )
    dist_dir = repo / "dist"
    tarballs = sorted(dist_dir.glob("roleplay_slim-*.tar.gz"))
    if not tarballs:
        raise RuntimeError("hatch build produced no roleplay_slim-*.tar.gz in dist/")
    return tarballs[-1]


def _list_members(tarball: pathlib.Path) -> list[str]:
    """Every file in the archive, with the top-level "<proj>-<version>/"
    directory stripped so paths compare against the allowlist directly."""
    members: list[str] = []
    with tarfile.open(tarball, "r:gz") as tf:
        for ti in tf.getmembers():
            if ti.isdir():
                continue
            parts = ti.name.split("/", 1)
            name = parts[1] if len(parts) > 1 else parts[0]
            if name == "pax_global_header":
                continue
            members.append(name)
    return members


def _covered(path: str, patterns: list[str]) -> bool:
    """Does an archive member path satisfy the allowlist?

    A pattern ending in ``/`` is a directory prefix match
    (``src/foo/bar.py`` matches ``src/``); any other pattern is an exact
    file match (``README.md`` matches only ``README.md``).
    """
    for p in patterns:
        if p.endswith("/"):
            if path == p.rstrip("/") or path.startswith(p):
                return True
        elif path == p:
            return True
    return False


def _on_disk_has_content(repo: pathlib.Path, pattern: str) -> bool:
    """Does the source path behind an allowlist pattern actually have
    content in the working tree? (Used to distinguish a legitimately empty
    directory from a typo'd pattern.)"""
    if pattern.endswith("/"):
        p = repo / pattern.rstrip("/")
        return p.is_dir() and any(p.rglob("*"))
    return (repo / pattern).is_file()


def main() -> int:
    patterns = _load_include_patterns(REPO)
    dist_dir = REPO / "dist"

    with tempfile.TemporaryDirectory(prefix="rps-sdist-check-") as tmp:
        backups = _backup_existing_tarballs(dist_dir, pathlib.Path(tmp))
        tarball = _build_sdist(REPO)
        try:
            members = _list_members(tarball)
        finally:
            _restore_or_cleanup(dist_dir, backups)

    stray = sorted(
        m for m in members
        if m not in ALLOWED_METADATA and not _covered(m, patterns)
    )
    dead_patterns = sorted(
        p for p in patterns
        if _on_disk_has_content(REPO, p) and not any(_covered(m, [p]) for m in members)
    )

    ok = True
    if stray:
        ok = False
        print("ERROR — files in sdist NOT allowed by pyproject.toml:")
        for m in stray:
            print(f"  {m}")
    if dead_patterns:
        ok = False
        print("ERROR — allowlisted path has content on disk but shipped nothing:")
        for p in dead_patterns:
            print(f"  {p}")

    if ok:
        print(f"OK — sdist ({tarball.name}) contains {len(members)} files, "
              f"all inside the {len(patterns)}-entry allowlist")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
