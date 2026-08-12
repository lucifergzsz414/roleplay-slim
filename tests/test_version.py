"""The exported __version__ must agree with the installed package metadata.

This existed as a hand-written literal until 0.3.1 and sat at 0.1.0 through
two releases, so anything introspecting the installed version got a stale
answer. The literal is gone; this test is what keeps a second source of
truth from creeping back in.
"""

from importlib.metadata import version as pkg_version

import roleplay_slim


def test_version_matches_package_metadata():
    assert roleplay_slim.__version__ == pkg_version("roleplay-slim")


def test_version_is_not_the_uninstalled_placeholder():
    # A real test run installs the package (pip install -e ".[dev]"), so
    # hitting the PackageNotFoundError fallback means the import path is
    # resolving to a source tree that isn't the installed distribution.
    assert roleplay_slim.__version__ != "0.0.0.dev0"
