"""Local start/stop used by the all-in-one Windows app: token-protected graceful shutdown."""

import socket
import subprocess
import sys
import time

import httpx


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(tmp_path, port, ctl):
    return subprocess.Popen([sys.executable, "-m", "ems", "serve", "--demo", "--data-dir", str(tmp_path / "data"),
                             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
                             "--local-control-file", str(ctl)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_local_shutdown_with_token(tmp_path):
    port, ctl = _free_port(), tmp_path / "control.txt"
    proc = _serve(tmp_path, port, ctl)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise AssertionError(proc.stdout.read() if proc.poll() is not None else "server did not start")
        saved_port, token = ctl.read_text().split()
        assert saved_port == str(port)
        # A second instance on the same port refuses to start and leaves the control file alone.
        second = _serve(tmp_path / "b", port, tmp_path / "other.txt")
        assert second.wait(30) == 3 and not (tmp_path / "other.txt").exists()
        assert ctl.read_text().split()[1] == token
        assert httpx.post(f"{url}/api/v1/system/local-shutdown").status_code == 403
        assert httpx.post(f"{url}/api/v1/system/local-shutdown", headers={"x-ems-local-token": "wrong"}).status_code == 403
        assert httpx.post(f"{url}/api/v1/system/local-shutdown", headers={"x-ems-local-token": token}).json()["ok"]
        assert proc.wait(30) == 0
        assert not ctl.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
