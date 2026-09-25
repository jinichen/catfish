"""Exercise the packaged heartbeat with Windows capabilities (no Unix socket)."""
import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


def verify(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "loop_heartbeat_forever")
    code = compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), function], type_ignores=[])), str(path), "exec")
    writes = []
    socket = AsyncMock()
    for platform in ("nt", "posix"):
        writes.clear()
        socket.reset_mock()
        logger = Mock()
        socket.return_value = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
        virtual_path = SimpleNamespace(parent=SimpleNamespace(
            mkdir=lambda **kw: None, glob=lambda pattern: []), unlink=lambda **kw: None)
        namespace = {
            "DEFAULT_HEARTBEAT_INTERVAL_S": 1,
            "asyncio": SimpleNamespace(to_thread=asyncio.to_thread, sleep=asyncio.sleep,
                CancelledError=asyncio.CancelledError, **(
                    {"start_unix_server": socket} if platform == "posix" else {})),
            "os": SimpleNamespace(name=platform),
            "logger": logger,
            "get_loop_tick_socket_path": lambda home: virtual_path,
            "_tick_socket_handler": None,
            "write_loop_heartbeat": lambda **kw: writes.append(kw),
        }
        exec(code, namespace)
        asyncio.run(namespace["loop_heartbeat_forever"](should_continue=lambda: False))
        assert len(writes) == 1
        assert writes[0]["extra"]["loop_tick_socket"] is (platform == "posix")
        assert socket.await_count == (1 if platform == "posix" else 0)
        logger.warning.assert_not_called()


if __name__ == "__main__":
    verify(Path(sys.argv[1]) / "gateway" / "shutdown_watchdog.py")
