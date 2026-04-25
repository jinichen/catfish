"""命令行入口 —— Companion 通过 spawn_detached 起这个。"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import server


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catfish-tool-bridge",
        description="Bridge Companion App 调 hermes-agent tools",
    )
    default_sock = Path.home() / ".catfish" / "tool-bridge.sock"
    parser.add_argument(
        "--socket",
        type=Path,
        default=default_sock,
        help=f"Unix domain socket 路径（默认 {default_sock}）",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        server.init_and_serve(args.socket)
    except KeyboardInterrupt:
        print("\n[catfish-tool-bridge] shutdown", flush=True)
    except Exception as e:
        logging.getLogger("catfish.tool_bridge").exception("fatal")
        print(f"[catfish-tool-bridge] fatal: {e}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
