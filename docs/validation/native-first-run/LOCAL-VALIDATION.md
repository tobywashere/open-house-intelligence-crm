# Local automated validation

Initial review: `79b0a1e05f4ccb766fc532e694561d15f93c25a5`.
Final tested implementation: `e13030ef4a7d347da6520d9c9a5c38deb5e51e8a`.
This file records automated validation, not a fresh supported-target install.
A fresh independent reviewer identified three important gaps: native Inbox mock
intake, missing prepared-Python validation, and a simulated build-isolation test.
Follow-up fixes add shared environment validation, disable the native intake, and
exercise actual Vite output. Their final results are recorded below.

Host: Intel MacBook Pro, 8-core i9, 32 GB RAM, macOS 26.7; Python 3.13.7 from the
existing project environment and Node 22.19.0. These differ from the supported
installation matrix. No OpenClaw/Ollama or model was installed on this laptop.
Three pinned Linux/npm upstream artifacts were downloaded/streamed and checked;
see `runtime-provenance.json`. Cross-platform Python lock resolution is not proof
that Linux installation succeeds. CI is configured to install that lock with
required hashes on Ubuntu 24.04 / Python 3.12 and use Node 24.15.0.

| Check | Result | Boundary |
|---|---:|---|
| `python -m pytest backend/tests -q` | 804 passed | Real disposable SQLite/API/process tests; model responses simulated |
| `node --test openclaw-plugins/openhouse-read/test.mjs openclaw-plugins/openhouse-proposals/test.mjs` | 50 passed | Tool schema/transport contract |
| `npm --prefix dashboard ci --no-audit --no-fund` | exit 0 | Fresh worktree Node dependencies |
| `npm --prefix dashboard run build` | exit 0 | TypeScript and production build |
| `python scripts/test_native_read_browser.py --suite read` | 7 passed | Browser receipt rendering; synthetic responses |
| `python scripts/test_native_read_browser.py --suite auth` | 3 passed | Runtime credential boundary |
| `python scripts/test_native_read_browser.py --suite proposals` | 10 passed | Real protected API/DB/human decisions; simulated completion |
| `python scripts/test_native_read_browser.py --suite native-mode` | 3 passed | Shared-CRM read after approval, no General chat, relock, bootstrap validation and disabled native Inbox intake |

Actual build isolation: `python scripts/test_native_build_isolation.py` passed.
Its deliberate `--inject-dotenv` mutation failed because the compiled bundle
contained the adversarial destination; the normal build excluded both repository
and ambient destinations and retained the relative `/api` base.

The full backend suite includes occupied ports, OS-held locks, sanitized child
environments, setup preservation, drift detection, real subprocess supervision,
SIGTERM, late exits, descendant termination and a real held relay POST returning
409 after human closure. Focused tests initially exposed and then verified fixes
for equals-form CLI overrides, setup descendant leaks on timeout, and macOS
unreaped-process cleanup. They are regression evidence, not live model results.
The five FastAPI/Starlette warnings are pre-existing API deprecations.

Full local logs and the execution ledger are retained in the ignored
`.superpowers/sdd/2026-10-05-native-first-run/` worktree workspace. That workspace
also retains failed diagnostic attempts. Private runtime logs and credentials are
not part of this report or the committed source.

Outstanding gates: independent clean WSL install; actual pinned gateway/model
startup; live read/proposal reliability; real interruption/late-call recovery;
measured hardware/latency; external-network-disabled operation; reviewed sanitized
preservation/cleanup evidence. Do not call this a verified first-run release yet.

## Review decisions and remaining limits

- Preserve the ignored execution ledger/evidence for cross-computer reproduction;
  cost: retained disk use rather than automatic cleanup.
- Keep `.venv-native` at a stable checkout path; cost: relocation needs deliberate
  repair, not moving a virtual environment.
- Share `InstallError` in one module to avoid import cycles; cost: one small module.
- Use the pinned CLI's authenticated read-only health RPC; cost: this behavior is
  version-specific and must be rechecked before an OpenClaw upgrade.
- Terminate preparation process groups on timeout; cost: timed-out installs stop
  rather than finishing invisibly in the background.
- Disable unconfigured scan, voice, lead processing and Inbox note intake in native
  mode; cost: optional workflows require later implementation/setup.
- Fault acceptance explicitly backs up/regenerates/restores a disposable manifest
  with the altered relay target; cost: an extra declared test-only preparation step,
  with no production bypass flag.
- Fresh Linux/GPU/model/interruption/offline evidence remains an external gate;
  cost: this candidate is not a verified first-run release.
- Same-user malicious processes remain outside the file-permission boundary;
  cost: they can access state owned by that user.
- Other platforms, upgrades, migrations and relocation repair remain deferred;
  cost: the supported first-install path must be used.
- Upstream OpenClaw's transitive npm tree is recorded after installation, not
  claimed fully locked by the root tarball hash; cost: acceptance must disclose it.

Deferred minor: Daily Summary **Refresh now** and Briefing **Remember it** still
attempt General chat, which native mode rejects. Their UI needs availability
labels; they cannot bypass the server's native-only chat guard.
