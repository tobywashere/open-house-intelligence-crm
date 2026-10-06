#!/usr/bin/env python3
"""Run browser boundary tests against a disposable CRM on Linux, macOS, or WSL.

Build dashboard/dist and install Playwright Chromium first. This runner needs
the backend's Python dependencies, but no OpenClaw, Ollama, or credentials.
"""
import argparse
import os
import secrets
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path, help='Optional output directory for synthetic proposal screenshots')
    parser.add_argument('--suite', choices=['read', 'auth', 'proposals', 'native-mode'], default='read')
    args = parser.parse_args()
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
        app = "app.main:app"
        human_token = secrets.token_hex(32)
        if args.suite in ('proposals','native-mode'):
            app = "native_proposal_browser_fixture:app"
            server_env.update(
                PYTHONPATH=os.pathsep.join([str(ROOT / 'scripts'), str(ROOT / 'backend')]),
                OHI_API_TOKEN=human_token, OHI_AGENT_API_TOKEN=secrets.token_hex(32),
                NATIVE_PROPOSAL_GATEWAY_URL="http://127.0.0.1:1",
                NATIVE_PROPOSAL_GATEWAY_TOKEN=secrets.token_hex(32),
                CRM_FIXTURE_URL=url,
            )
        if args.suite == 'native-mode':server_env['OHI_NATIVE_ONLY']='1'
        with log_path.open("w") as log:
            server = subprocess.Popen([
                sys.executable, "-m", "uvicorn", app, "--app-dir", "backend",
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
                if args.suite in ('proposals','native-mode'):
                    browser_env['CRM_TEST_HUMAN_TOKEN'] = human_token
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        browser_env['CRM_TEST_SCREENSHOTS'] = str(args.screenshots.resolve())
                    print("SIMULATED inference only; real authenticated agent HTTP, durable DB and human approvals", flush=True)
                suite = {'read': 'native-read', 'auth': 'auth', 'proposals': 'native-proposals', 'native-mode':'native-mode'}[args.suite]
                result = subprocess.run([
                    "node", "--test", f"tests/{suite}.browser.cjs",
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
