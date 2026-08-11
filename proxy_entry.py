"""Entry point for PyInstaller packaging.

The proxy's __main__.py uses package-relative imports (``from ..config``)
which fail when PyInstaller freezes it as a standalone script. This tiny
wrapper imports the main() function through the installed package path so
PyInstaller can follow the normal import chain.

Built with ``--console`` (not ``--noconsole``) so the proxy's compression-rate
log line (``[roleplay-slim] request #N | ...``) is actually visible in the
launcher's console window — a real console means real stdio, so no
sys.stdout/stderr workarounds are needed here.
"""

from roleplay_slim.proxy.__main__ import main

if __name__ == "__main__":
    main()
