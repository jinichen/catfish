"""Launch the packaged daemon with real Hermes, then verify actual IPC health.

Used on the Windows runner before MSI packaging, not a mocked import test.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--hermes', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='catfish-ipc-smoke-') as tmp:
        endpoint = Path(tmp) / 'bridge.endpoint'
        log = Path(tmp) / 'bridge.log'
        env = dict(os.environ, PYTHONPATH=str(args.source),
                   HERMES_AGENT_PATH=str(args.hermes), CATFISH_MCP_AUTOSTART='',
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        with log.open('wb') as output:
            proc = subprocess.Popen([args.python, '-m', 'catfish_tool_bridge', '--socket', str(endpoint)],
                                    env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                last_error = 'no endpoint'
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError(f'daemon exited {proc.returncode}')
                    try:
                        if os.name == 'nt':
                            conn = socket.create_connection(('127.0.0.1', int(endpoint.read_text())), timeout=3)
                        else:
                            conn = socket.socket(socket.AF_UNIX)
                            conn.settimeout(3)
                            conn.connect(str(endpoint))
                        with conn, conn.makefile('rb') as response:
                            conn.sendall(b'{"jsonrpc":"2.0","id":1,"method":"health"}\n')
                            value = json.loads(response.readline())
                        health = value.get('result', {})
                        if (value.get('id') != 1 or not health.get('ok')
                                or not health.get('tool_count', 0) > health.get('native_tool_count', 0) > 0):
                            raise RuntimeError(f'invalid health response: {value}')
                        print('Packaged Tool Bridge health:', json.dumps(value))
                        return
                    except (OSError, ValueError) as error:
                        last_error = str(error)
                        time.sleep(0.25)
                raise RuntimeError(f'IPC readiness timeout: {last_error}')
            except Exception:
                print(log.read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == '__main__':
    main()
