"""Make the repo root importable for the test suite.

The fidelity/corpus tests import the ``benchmark`` package (test tooling
sitting at the repo root, not under src/). ``pytest`` — the console script,
what CI runs — does not add the current directory to sys.path, so those
imports fail with ModuleNotFoundError unless the root is on the path. pytest
inserts the directory of this conftest.py into sys.path, which fixes it for
every invocation mode.
"""
