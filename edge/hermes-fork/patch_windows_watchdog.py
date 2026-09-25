"""Keep file heartbeats on Windows without attempting a Unix loop witness."""
from pathlib import Path
import sys


def patch(source: str) -> str:
    old = '''        tick_server = await asyncio.start_unix_server(
            _tick_socket_handler, path=str(tick_socket_path)
        )'''
    new = '''        # CATFISH-WINDOWS-WATCHDOG: preserve file heartbeat / UNKNOWN verdict.
        if os.name != "nt":
            tick_server = await asyncio.start_unix_server(
                _tick_socket_handler, path=str(tick_socket_path)
            )'''
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("Watchdog upstream changed; review Windows heartbeat patch")
    return source.replace(old, new)


if __name__ == "__main__":
    path = Path(sys.argv[1]) / "gateway" / "shutdown_watchdog.py"
    result = patch(path.read_text(encoding="utf-8"))
    compile(result, str(path), "exec")
    path.write_text(result, encoding="utf-8")
