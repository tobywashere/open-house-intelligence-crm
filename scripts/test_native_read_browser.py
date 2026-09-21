#!/usr/bin/env python3
"""Run browser boundary tests against a disposable CRM on Linux, macOS, or WSL.

Build dashboard/dist and install Playwright Chromium first. This runner needs
the backend's Python dependencies, but no OpenClaw, Ollama, or credentials.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != "posix":
        raise SystemExit("Run this isolated browser fixture in WSL on Windows.")
    if not (ROOT / "dashboard/dist/index.html").is_file():
        raise SystemExit("Build the dashboard first: cd dashboard && npm run build")

    with tempfile.TemporaryDirectory(prefix="ohi-browser-") as directory, socket.socket() as listener:
        # Pass the bound socket to our child: no fixed port, reuse of another
        # server, or bind/release race that could point tests at a real CRM.
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        log_path = Path(directory) / "backend.log"
        server_env = {key: os.environ[key] for key in (
            "PATH", "LANG", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
        ) if key in os.environ}
        server_env.update(
            DB_PATH=str(Path(directory) / "fixture.db"),
            AGENT_MODE="mock", INTEGRATIONS_MODE="off", INTEGRATIONS_POLLER="off",
            PYTHONDONTWRITEBYTECODE="1",
        )
        with log_path.open("w") as log:
            server = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                "--fd", str(listener.fileno()), "--log-level", "warning",
            ], cwd=ROOT, env=server_env, pass_fds=(listener.fileno(),), stdout=log, stderr=log)
            try:
                # Do not route this localhost readiness check through ambient proxies.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("The isolated CRM exited before becoming ready")
                    try:
                        with opener.open(url + "/api/health", timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The isolated CRM did not become ready in 20 seconds")
                    time.sleep(0.1)

                print("Browser boundary fixture ready (temporary CRM; integrations off)", flush=True)
                browser_env = {key: os.environ[key] for key in (
                    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ", "CI",
                    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DISPLAY", "WAYLAND_DISPLAY",
                    "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
                    "PLAYWRIGHT_MODULE", "PLAYWRIGHT_BROWSERS_PATH", "BROWSER_CHANNEL",
                ) if key in os.environ}
                browser_env["CRM_TEST_URL"] = url
                result = subprocess.run([
                    "node", "--test", "tests/native-read.browser.cjs",
                ], cwd=ROOT / "dashboard", env=browser_env, timeout=180)
                if result.returncode:
                    print(log_path.read_text(), file=sys.stderr)
                return result.returncode
            except Exception:
                print(log_path.read_text(), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
