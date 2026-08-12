from importlib.metadata import PackageNotFoundError, version as _pkg_version

from .compressor import compress
from .config import CompressorConfig, ProxyConfig
from .stats import CompressionStats, estimate_messages_tokens, estimate_tokens

__all__ = [
    "compress",
    "CompressorConfig",
    "ProxyConfig",
    "CompressionStats",
    "estimate_messages_tokens",
    "estimate_tokens",
]

try:
    # Single source of truth: the version in pyproject.toml, as recorded in
    # the installed distribution's metadata. Hand-maintaining a literal here
    # is what let __version__ sit at 0.1.0 for two releases.
    __version__ = _pkg_version("roleplay-slim")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled source tree
    __version__ = "0.0.0.dev0"
