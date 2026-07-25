from __future__ import annotations

import argparse
import logging

from ..config import ProxyConfig
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="roleplay-slim-proxy")
    parser.add_argument("--config", help="Path to a TOML config file", default=None)
    parser.add_argument("--port", type=int, default=None, help="Override config port")
    parser.add_argument("--host", default=None, help="Override config host")
    parser.add_argument("--upstream", default=None, help="Override upstream base URL")
    args = parser.parse_args()

    config = ProxyConfig.from_toml(args.config) if args.config else ProxyConfig()
    if args.port is not None:
        config.port = args.port
    if args.host is not None:
        config.host = args.host
    if args.upstream is not None:
        config.upstream_base_url = args.upstream

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[roleplay-slim] %(message)s"))
    rs_logger = logging.getLogger("roleplay_slim")
    rs_logger.setLevel(logging.INFO)
    rs_logger.addHandler(handler)
    rs_logger.propagate = False

    import uvicorn

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
