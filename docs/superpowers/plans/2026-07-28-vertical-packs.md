# Vertical Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CRM adaptable to any sales vertical via a swappable config pack + knowledge corpus, with UI for uploading knowledge docs and tuning the daily research keywords — per `docs/superpowers/specs/2026-07-28-vertical-packs-design.md`.

**Architecture:** A `verticals/<name>/pack.json` supplies everything industry-bound (funnel stages and their rules, field display labels, persona names, UI copy strings, daily-research scope). The backend serves the active pack at `GET /api/vertical`; the dashboard fetches it once and resolves `pack value → built-in default`, so a missing or partial pack degrades to today's real-estate behavior. Knowledge docs get upload/list/delete endpoints plus a management panel; research scope gets a settings row and an editor. No schema columns change — `docs/CONTRACT.md` stays frozen.

**Tech Stack:** Python 3.11+/FastAPI/sqlite3/pytest (backend), TypeScript/React 18/Vite (dashboard), stdlib-only for `skills/*/tools.py`, JSON config (no new deps anywhere).

## Global Constraints

- **No new dependencies**, backend or dashboard. `backend/requirements.txt` stays fastapi/uvicorn/httpx/pytest.
- **No schema column renames or additions to `leads`.** The frozen contract in `docs/CONTRACT.md` must stay exactly true — it currently claims every REST write audits except the named `POST /chat` carve-out, and exactly two reads audit (`GET /availability`, `GET /leads/{id}/duplicates`). New write endpoints MUST audit; new read endpoints MUST NOT.
- **`get_conn()` is one BEGIN IMMEDIATE transaction per block** — never do file I/O, indexing, or network calls inside one. `backend/tests/test_lock_release.py` enforces this class of rule.
- **The real-estate pack must be a provable no-op**: the existing suite passes unchanged with it active. Baseline is **154 backend tests**.
- Timestamps are naive local wall-clock (`YYYY-MM-DDTHH:MM:SS`, no `Z`); use `toNaiveLocal`/`localDateKey` in `dashboard/src/api.ts` for anything written.
- New `/api/*` routes sit behind the existing `OHI_API_TOKEN` middleware automatically — do not special-case them.
- The 12 locked retrieval acceptance queries in `backend/tests/test_knowledge.py` must not be modified.
- Product name in user-facing text is **OpenHouse Intelligence**.
- Gates every task: `cd backend && ../.venv/bin/python -m pytest tests/ -q` and `cd dashboard && npx tsc -b && npm run build`.
- Parallel sessions commit to this repo: start every task with `git log --oneline -3; git status --short`, re-read files before editing, match line-number anchors by content. Reality wins over this plan; record deviations.

---

### Task 1: Pack schema, loader, and the real-estate pack

**Files:**
- Create: `verticals/real-estate/pack.json`, `backend/app/vertical.py`
- Test: `backend/tests/test_vertical.py`

**Interfaces:**
- Produces: `load_pack() -> dict` in `backend/app/vertical.py`, returning a fully-resolved pack (pack values merged over built-in defaults, so callers never handle missing keys). `VERTICAL` env var selects the pack directory name; `VERTICALS_DIR` overrides the search root (tests use it). `DEFAULT_PACK: dict` is the built-in real-estate fallback.

- [ ] **Step 1: Write the failing tests**

```python
"""Pack loading must degrade to real-estate defaults, never crash."""
import json
import pytest
from app import vertical


def test_missing_pack_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "does-not-exist")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["stages"][0]["key"] == "new"
    assert pack["labels"]["budget"] == "Budget"


def test_partial_pack_merges_over_defaults(tmp_path, monkeypatch):
    d = tmp_path / "partial"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"labels": {"budget": "Deal size"}}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "partial")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["labels"]["budget"] == "Deal size"      # overridden
    assert pack["labels"]["area"] == "Area"             # defaulted
    assert len(pack["stages"]) == 6                     # defaulted


def test_malformed_json_falls_back_and_does_not_raise(tmp_path, monkeypatch):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "pack.json").write_text("{ not json")
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "broken")
    vertical.clear_cache()
    assert vertical.load_pack()["labels"]["budget"] == "Budget"


def test_unknown_stage_rule_is_dropped_not_fatal(tmp_path, monkeypatch):
    d = tmp_path / "weird"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"stages": [
        {"key": "new", "label": "New", "rule": {"type": "all"}},
        {"key": "bogus", "label": "Bogus", "rule": {"type": "telepathy"}},
    ]}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "weird")
    vertical.clear_cache()
    stages = vertical.load_pack()["stages"]
    assert [s["key"] for s in stages] == ["new"]


def test_shipped_real_estate_pack_matches_defaults():
    """The extracted pack must be byte-equivalent in effect to the built-in
    defaults — that is what makes this refactor a provable no-op."""
    vertical.clear_cache()
    import os
    os.environ.pop("VERTICALS_DIR", None)
    os.environ["VERTICAL"] = "real-estate"
    vertical.clear_cache()
    assert vertical.load_pack() == vertical.DEFAULT_PACK
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_vertical.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.vertical'`.

- [ ] **Step 3: Write `backend/app/vertical.py`**

`DEFAULT_PACK` holds today's real-estate behavior, transcribed from the live code — read `dashboard/src/funnel.ts` (stage keys/labels and the derived rules) and `dashboard/src/briefing.ts`'s `PERSONA_STYLE` before writing, and copy the actual values rather than these illustrative ones:

```python
"""Vertical pack loading. A pack is verticals/<name>/pack.json; every key is
optional and merges over DEFAULT_PACK, so a missing/partial/broken pack degrades
to real-estate behavior instead of failing. Nothing here touches the DB."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Stage rule vocabulary — anything else is dropped by _sanitize_stages():
#   {"type": "all"}                              every lead
#   {"type": "status_at_least", "status": "..."} status rank >= that status
#   {"type": "status_at_least_or_score", "status": "...", "min_score": 70}
#   {"type": "event_type_or_status", "event_type": "offer", "status": "closed"}
#   {"type": "status_is", "status": "closed"}
KNOWN_RULE_TYPES = {"all", "status_at_least", "status_at_least_or_score",
                    "event_type_or_status", "status_is"}

DEFAULT_PACK: dict = {
    "name": "real-estate",
    "display_name": "Real estate",
    "stages": [...],      # transcribe from funnel.ts:135-140 + the filters above them
    "labels": {"budget": "Budget", "area": "Area", "timeline": "Timeline",
               "intent": "Intent"},
    "intent_values": [...],   # buy | sell | browse | unknown, with labels
    "personas": [...],        # keys from briefing.ts PERSONA_STYLE, with a default
    "copy": {...},            # keys filled in Task 3; start with the ones Task 3 lists
    "research": {...},        # filled in Task 5 from prompts/seattle-real-estate-news-reporter.md
}

_cache: dict | None = None


def clear_cache() -> None:
    global _cache
    _cache = None


def _verticals_dir() -> Path:
    return Path(os.environ.get("VERTICALS_DIR", REPO_ROOT / "verticals"))


def _sanitize_stages(stages) -> list:
    out = []
    for s in stages or []:
        if not isinstance(s, dict) or "key" not in s:
            continue
        rule = s.get("rule") or {}
        if rule.get("type") not in KNOWN_RULE_TYPES:
            logging.warning("vertical pack: dropping stage %r with unknown rule %r",
                            s.get("key"), rule.get("type"))
            continue
        out.append(s)
    return out


def load_pack() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    pack = json.loads(json.dumps(DEFAULT_PACK))   # deep copy
    path = _verticals_dir() / os.environ.get("VERTICAL", "real-estate") / "pack.json"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("vertical pack at %s unreadable (%s) — using defaults", path, exc)
        raw = {}
    for key, value in raw.items():
        if key == "stages":
            sanitized = _sanitize_stages(value)
            if sanitized:
                pack["stages"] = sanitized
        elif isinstance(value, dict) and isinstance(pack.get(key), dict):
            pack[key].update(value)
        elif value:
            pack[key] = value
    _cache = pack
    return pack
```

Then write `verticals/real-estate/pack.json` containing exactly the `DEFAULT_PACK` values (the last test asserts equality, so any drift fails).

- [ ] **Step 4: Run the suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/ -q`
Expected: 154 prior + 5 new, all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/vertical.py backend/tests/test_vertical.py verticals/real-estate/pack.json
git commit -m "feat: vertical pack loader with real-estate defaults"
```

---

### Task 2: `GET /api/vertical` + dashboard pack context

**Files:**
- Create: `backend/app/routers/vertical.py`, `dashboard/src/vertical.ts`
- Modify: `backend/app/main.py` (register router), `docs/CONTRACT.md` (§2 row, §5 env)
- Test: `backend/tests/test_vertical.py` (extend)

**Interfaces:**
- Consumes: `load_pack()` from Task 1.
- Produces: `GET /api/vertical` → the resolved pack JSON. Dashboard-side `dashboard/src/vertical.ts` exports `loadVertical(): Promise<Pack>`, `pack(): Pack` (sync accessor returning the built-in default until loaded), and `copy(key: string, fallback: string): string`. Tasks 3/4/6 consume those.

- [ ] **Step 1: Write the failing tests**

```python
def test_vertical_endpoint_returns_resolved_pack(client):
    r = client.get("/api/vertical")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "real-estate"
    assert len(body["stages"]) == 6
    assert body["labels"]["budget"] == "Budget"


def test_vertical_endpoint_writes_no_audit_row(client):
    """It is a READ. CONTRACT §3 says exactly two reads audit; this isn't one."""
    before = len(client.get("/api/audit?limit=500").json())
    client.get("/api/vertical")
    assert len(client.get("/api/audit?limit=500").json()) == before
```

- [ ] **Step 2: Run to verify it fails** — 404, route doesn't exist.

- [ ] **Step 3: Implement**

```python
"""Serves the active vertical pack to the dashboard. Read-only, no audit row
(CONTRACT §3: exactly two reads audit, and this is not one of them)."""
from fastapi import APIRouter

from ..vertical import load_pack

router = APIRouter(tags=["vertical"])


@router.get("/vertical")
def get_vertical() -> dict:
    return load_pack()
```

Register in `main.py` beside the other routers. Then `dashboard/src/vertical.ts`:

```ts
/** The active vertical pack. Fetched once at startup; every consumer resolves
 *  pack value -> built-in default, so the UI renders correctly even if the
 *  request fails. */
import { api } from './api'

export interface Stage { key: string; label: string; rule: Record<string, unknown> }
export interface Pack {
  name: string; display_name: string
  stages: Stage[]
  labels: Record<string, string>
  intent_values: { value: string; label: string }[]
  personas: string[]
  copy: Record<string, string>
}

const BUILT_IN: Pack = { /* mirror of DEFAULT_PACK — same values as verticals/real-estate/pack.json */ }
let active: Pack = BUILT_IN

export const pack = (): Pack => active
export const copy = (key: string, fallback: string): string => active.copy?.[key] ?? fallback

export async function loadVertical(): Promise<Pack> {
  try {
    active = { ...BUILT_IN, ...(await api.vertical<Pack>()) }
  } catch {
    active = BUILT_IN     // offline/401/404 — the UI still works, in real-estate copy
  }
  return active
}
```

Add `vertical: <T>() => req<T>('/vertical')` to `api.ts`'s exported object, and call `loadVertical()` once in `App.tsx`'s startup effect before first render of pack-dependent UI (store it in state so components re-render when it resolves).

Add the `GET /api/vertical` row to `docs/CONTRACT.md` §2 (marked additive 2026-07-28) and `VERTICAL` / `VERTICALS_DIR` to `.env.example` in a new Vertical group.

- [ ] **Step 4: Gates** — backend suite green; `npx tsc -b && npm run build` green.
- [ ] **Step 5: Commit** — `feat: serve the active vertical pack to the dashboard`

---

### Task 3: Copy strings and field labels from the pack

**Files:**
- Modify: `dashboard/src/components/BookingCard.tsx`, `dashboard/src/components/ChatPanel.tsx`, `dashboard/src/components/BriefingSection.tsx`, `dashboard/src/pages/Lead.tsx`, `dashboard/src/pages/Inbox.tsx`, `dashboard/src/briefing.ts` (PERSONA_STYLE), `verticals/real-estate/pack.json`, `backend/app/vertical.py` (DEFAULT_PACK copy keys)
- Test: `dashboard` typecheck/build + `backend/tests/test_vertical.py` (key-coverage test)

**Interfaces:**
- Consumes: `copy(key, fallback)` and `pack()` from Task 2.

- [ ] **Step 1: Inventory the strings**

Run and save the output into your report — it is the checklist for this task:

```bash
cd dashboard && grep -rniE "tour|buyer|home |listing|realtor|property|showing|open house|seller" src --include='*.tsx' --include='*.ts' -n | grep -v '^\S*: *//'
```

Known counts as of 2026-07-28: 23 "tour", 14 "buyer", 9 "home ", 4 "showing", 4 "seller", 3 "listing", 2 "realtor", 2 "open house". Comments do not need changing — only rendered strings and user-visible placeholder text.

- [ ] **Step 2: Write the failing coverage test**

```python
def test_every_copy_key_used_by_the_dashboard_exists_in_the_pack():
    """Guards against a `copy('x', ...)` call whose key nobody added to the pack —
    the fallback would silently mask it."""
    import re
    from pathlib import Path
    from app.vertical import DEFAULT_PACK
    src = Path(__file__).resolve().parents[2] / "dashboard" / "src"
    used = set()
    for f in src.rglob("*.ts*"):
        used |= set(re.findall(r"copy\(\s*'([a-z0-9_.]+)'", f.read_text()))
    missing = used - set(DEFAULT_PACK["copy"])
    assert not missing, f"copy keys used in the dashboard but absent from the pack: {sorted(missing)}"
```

- [ ] **Step 3: Run it** — passes trivially now (no `copy()` calls yet); it becomes meaningful as you convert strings, and will fail the moment you add a key to the UI without the pack.

- [ ] **Step 4: Convert each rendered string**

Pattern — `BookingCard.tsx:60,82` today:

```tsx
<div className="text-accent font-medium">✓ Tour booked</div>
<h2 className="text-sm font-semibold">Book a tour</h2>
```

becomes:

```tsx
<div className="text-accent font-medium">✓ {copy('booking.booked', 'Tour booked')}</div>
<h2 className="text-sm font-semibold">{copy('booking.cta', 'Book a tour')}</h2>
```

with `"booking.booked": "Tour booked"` and `"booking.cta": "Book a tour"` added to both `DEFAULT_PACK["copy"]` and `verticals/real-estate/pack.json`. Use dotted keys namespaced by area (`booking.*`, `chat.*`, `inbox.*`, `lead.*`, `briefing.*`). `ChatPanel.tsx:181-182`'s example prompts are user-visible and must be converted (they name Kirkland/Redmond and "buyers"). Field labels come from `pack().labels[...]` rather than `copy()`. `PERSONA_STYLE` in `briefing.ts:73` keeps its color mapping but its keys come from `pack().personas`, with the existing `'Home Buyer'` fallback preserved.

- [ ] **Step 5: Gates + the no-op check** — backend green (incl. the new coverage test), `npx tsc -b && npm run build` green, and confirm the running dashboard renders identically to before under the real-estate pack.
- [ ] **Step 6: Commit** — `feat: dashboard copy and field labels come from the vertical pack`

---

### Task 3b: Mock briefing generator from the pack

**Files:**
- Modify: `dashboard/src/briefing.ts` (`personaOf` ~:65-70, recommendation templates ~:111-127, schedule titles ~:171), `backend/app/vertical.py` (`DEFAULT_PACK`), `verticals/real-estate/pack.json`
- Test: `backend/tests/test_vertical.py` (extend)

**Scope note (added 2026-07-28 after Task 3):** this task also owns two things Task 3 deliberately left, because both need restructuring rather than string swaps — (a) the **app wordmark** in `App.tsx:111`, currently a hardcoded two-tone "Open House Intelligence" split across a plain span and a `.brand-gradient` span, with a stale unused `copy.app_name` key ("Open Intelligence CRM") that must be reconciled; make the wordmark pack-driven (e.g. `brand.name` + `brand.name_accent`) while preserving the gradient treatment; and (b) the generated narrative prose in `dashboard/src/summary.ts` and `dashboard/src/insights.ts`, which is the same class of per-lead generated real-estate copy as the briefing generator.

**Why:** `briefing.ts` contains the client-side mock briefing generator — it produces a plausible briefing when the agent has not posted one, which is exactly what the README Quickstart shows a new evaluator in mock mode. Today it infers real-estate personas ("First-Time Buyer" under $700k, "Seller" when `intent === 'sell'`), writes real-estate recommendations ("Ask about schools first; keep the shortlist to three homes"), and titles schedule blocks "Showing" / "Listing appointment". Under a recruiting pack the demo would still generate real-estate briefings — the exact thing this effort exists to fix. This is restructuring, not string swaps, which is why it is its own task.

**Interfaces:**
- Consumes: `pack()` from Task 2.
- Produces: pack keys `persona_rules[]`, `persona_recommendations{}`, `schedule_titles{}` present in `DEFAULT_PACK` and every shipped pack.

- [ ] **Step 1: Read the current generator.** `dashboard/src/briefing.ts:60-175`. Note exactly which lead fields each persona rule reads (`intent`, `budget`, `score`, `area`) and in what order the branches fall through — order matters, first match wins.

- [ ] **Step 2: Design the pack schema for it.** `persona_rules` is an ordered list evaluated first-match-wins; each entry is `{persona, when}` where `when` uses a small declarative vocabulary sufficient for today's rules — at minimum `{field, op, value}` with ops `eq` / `lt` / `gte`, plus a final unconditional default. Reproduce today's real-estate rules exactly in `DEFAULT_PACK`; anything the vocabulary cannot express is a finding to report, not something to approximate silently.

- [ ] **Step 3: Write the failing test**

```python
def test_persona_rules_reproduce_the_shipped_inference():
    """The mock generator's persona inference must be pack-driven and must
    still produce today's real-estate personas for the real-estate pack."""
    from app.vertical import DEFAULT_PACK
    rules = DEFAULT_PACK["persona_rules"]
    assert rules[-1].get("when") is None, "last rule must be the unconditional default"
    personas = {r["persona"] for r in rules}
    assert {"Seller", "First-Time Buyer", "Home Buyer"} <= personas
    for r in DEFAULT_PACK["persona_recommendations"]:
        assert r in personas, f"recommendation for unknown persona {r}"
    assert set(DEFAULT_PACK["schedule_titles"]) >= {"default", "sell"}
```

- [ ] **Step 4: Run it** — FAIL, `KeyError: 'persona_rules'`.

- [ ] **Step 5: Implement.** Add the three blocks to `DEFAULT_PACK` and `verticals/real-estate/pack.json` (the equality test keeps them in sync). In `briefing.ts`, replace the hardcoded branches with an evaluator over `pack().persona_rules`, look recommendations up by resolved persona with a generic fallback, and take schedule titles from `pack().schedule_titles` keyed by intent. Every lookup keeps a fallback so a partial pack still renders.

- [ ] **Step 6: Verify the no-op.** With the real-estate pack active, a seeded mock-mode briefing must be identical to before — same personas, same recommendations, same schedule titles. State in your report how you confirmed it (compare rendered output before/after on the same seed data).

- [ ] **Step 7: Gates + commit** — backend green, `npx tsc -b && npm run build` green. `feat: mock briefing generator is pack-driven`

---

### Task 4: Funnel stages from the pack

**Files:**
- Modify: `dashboard/src/funnel.ts:118-160`
- Test: `backend/tests/test_vertical.py` (rule-vocabulary test); dashboard gates

**Interfaces:**
- Consumes: `pack()` from Task 2.
- Produces: `stagesFromPack(leads, eventsByLead, stages)` in `funnel.ts`, returning `FunnelStage[]` — replaces the six hardcoded entries at `funnel.ts:135-140`.

- [ ] **Step 1: Read the current logic** — `funnel.ts:128-140` computes `reachedContact`/`qualified`/`toured`/`offered`/`closed` via `RANK[l.status]` comparisons plus the `score >= 70` and `offer`-event conventions. Your rule evaluator must reproduce all five exactly.

- [ ] **Step 2: Write the failing test** (backend-side, since the rule vocabulary lives in the pack)

```python
def test_default_pack_stage_rules_cover_the_shipped_funnel():
    """The six real-estate stages must be expressible in the rule vocabulary —
    if a rule type is missing, funnel.ts can't reproduce today's behavior."""
    from app.vertical import DEFAULT_PACK, KNOWN_RULE_TYPES
    keys = [s["key"] for s in DEFAULT_PACK["stages"]]
    assert keys == ["new", "contacted", "qualified", "tours", "offers", "closed"]
    for s in DEFAULT_PACK["stages"]:
        assert s["rule"]["type"] in KNOWN_RULE_TYPES
    qualified = next(s for s in DEFAULT_PACK["stages"] if s["key"] == "qualified")
    assert qualified["rule"] == {"type": "status_at_least_or_score",
                                 "status": "contacted", "min_score": 70}
```

- [ ] **Step 3: Implement the evaluator in `funnel.ts`**

```ts
const RULE_EVAL: Record<string, (l: Lead, r: any, ev: Map<number, LeadProfile['events']>) => boolean> = {
  all: () => true,
  status_is: (l, r) => l.status === r.status,
  status_at_least: (l, r) => RANK[l.status] >= RANK[r.status],
  status_at_least_or_score: (l, r) =>
    RANK[l.status] >= RANK[r.status] + 1 ||
    (RANK[l.status] >= RANK[r.status] && (l.score ?? 0) >= r.min_score),
  event_type_or_status: (l, r, ev) =>
    (ev.get(l.id) ?? []).some((e) => e.type === r.event_type) || l.status === r.status,
}

function stagesFromPack(leads: Lead[], eventsByLead: Map<number, LeadProfile['events']>,
                        stages: Stage[]): FunnelStage[] {
  return stages.map((s) => {
    const evaluate = RULE_EVAL[s.rule.type as string]
    const matched = evaluate ? leads.filter((l) => evaluate(l, s.rule, eventsByLead)) : []
    return { key: s.key, label: s.label, count: matched.length }
  })
}
```

Verify `status_at_least_or_score` reproduces `funnel.ts:130`'s `RANK[l.status] >= 2 || (RANK[l.status] >= 1 && score >= 70)` exactly for `{status: "contacted", min_score: 70}` — if the arithmetic doesn't line up against the real `RANK` map, adjust the rule shape and the DEFAULT_PACK value together, and say so in your report. The downstream `conversions`/`bottleneck`/`overallPct` code consumes `stages` and needs no change; `offerOf`/`parseOfferAmount` stay for the Top-opportunities card.

- [ ] **Step 4: Gates** — backend green; `npx tsc -b && npm run build` green; funnel renders the same six stages with the same counts on seeded data (`bash scripts/dev.sh`, compare against a pre-change screenshot or the numbers in the KPI strip).
- [ ] **Step 5: Commit** — `feat: funnel stages and rules come from the vertical pack`

---

### Task 4b: Funnel's derived sections use the same stage evaluator

**Files:**
- Modify: `dashboard/src/funnel.ts` (the `reachedContact`/`qualified`/`toured`/`offered`/`closed` locals and their consumers: velocity, sources, next-best-actions, KPI strip)
- Test: no-op comparison harness (as in Task 4) + `backend/tests/test_vertical.py` if the pack gains keys

**Why:** Task 4 made the six funnel STAGES pack-driven, but the velocity cards, source-conversion rates, next-best-actions, and the KPI strip still compute from hardcoded filters that duplicate the same logic. Under a pack defining `qualified` as `min_score: 50`, the funnel bar would say 50 while the "Qualified buyers" KPI beside it still says 70 — two different numbers for the same concept on one screen. The same applies to `warmUntoured` (hardcodes "tours"), `sources.won` (hardcodes `RANK>=2`), `negotiating` (hardcodes offers), and velocity's `chain = ['new','contacted','meeting_booked','closed']` (hardcodes the status ladder). Unreachable while real-estate is the only pack; a live wrong-number bug the moment Task 8's packs land.

**Interfaces:**
- Produces: `matchedByStage(leads, eventsByLead, stages): Map<string, Lead[]>` exported from `funnel.ts`; `stagesFromPack` becomes a thin count-map over it.

- [ ] **Step 1: Extract the shared evaluator.** Refactor `stagesFromPack` so both it and the derived sections read one source of truth. `matchedByStage` returns the matched lead array per stage key; `stagesFromPack` maps it to `{key, label, count}`.

- [ ] **Step 2: Rewrite the five locals** as `byStage.get('<key>') ?? <today's hardcoded filter>`. The `??` fallback is required, not optional: a pack may omit a stage key entirely, and the derived sections must then degrade to current behavior rather than showing zero.

- [ ] **Step 3: Handle the status ladder.** Velocity's `chain` and `sources.won` assume the real-estate status progression. Derive what you can from the pack's stage order; where a genuine schema status is required (the `leads.status` enum is NOT pack-driven — it stays `new|contacted|meeting_booked|closed`), keep it and add a comment saying why. Do not invent pack keys for schema-level concepts.

- [ ] **Step 4: Prove the no-op.** Same harness style as Task 4: old locals vs new `byStage` lookups over the 15 seeded demo leads plus a synthetic cross of statuses × scores × offer-events. Every derived number — velocity days, source rates, action counts, all six KPIs — must be identical under the real-estate pack. Report the comparison count.

- [ ] **Step 5: Gates + commit** — `cd backend && ../.venv/bin/python -m pytest tests/ -q`, `cd dashboard && npx tsc -b && npm run build`. `feat: funnel derived sections read the same pack-driven stage evaluator`

---

### Task 5: Research settings — storage, API, and templated prompt

**Files:**
- Create: `backend/app/routers/settings.py`, `prompts/market-news-reporter.md.template`
- Modify: `backend/schema.sql` (new `settings` table), `backend/app/db.py` (`_migrate` creates it for existing DBs), `backend/app/vertical.py` (`research` block in DEFAULT_PACK), `docs/CONTRACT.md`
- Test: `backend/tests/test_research_settings.py`

**Interfaces:**
- Consumes: `load_pack()`.
- Produces: `GET /api/research-settings` → `{role, regions[], topics[], exclusions[], lookback_days, rendered_prompt}`; `PUT /api/research-settings` → same shape, persisted. `render_research_prompt(settings: dict) -> str` in `settings.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_defaults_come_from_the_pack_when_unset(client):
    r = client.get("/api/research-settings")
    assert r.status_code == 200
    body = r.json()
    assert "Seattle" in body["regions"]
    assert body["lookback_days"] == 7
    assert "Seattle" in body["rendered_prompt"]


def test_put_persists_and_rerenders(client):
    client.put("/api/research-settings", json={
        "role": "commercial insurance broker", "regions": ["Ontario"],
        "topics": ["carrier appetite"], "exclusions": ["personal lines"],
        "lookback_days": 14})
    body = client.get("/api/research-settings").json()
    assert body["regions"] == ["Ontario"]
    assert "Ontario" in body["rendered_prompt"]
    assert "Seattle" not in body["rendered_prompt"]


def test_put_is_audited(client):
    """CONTRACT §3: every REST write audits except the POST /chat carve-out."""
    client.put("/api/research-settings", json={"role": "x", "regions": ["y"],
               "topics": [], "exclusions": [], "lookback_days": 7})
    tools = [a["tool"] for a in client.get("/api/audit?limit=50").json()]
    assert "update_research_settings" in tools


def test_get_is_not_audited(client):
    before = len(client.get("/api/audit?limit=500").json())
    client.get("/api/research-settings")
    assert len(client.get("/api/audit?limit=500").json()) == before


def test_bounds_rejected(client):
    for bad in ({"lookback_days": 0}, {"lookback_days": 400}, {"regions": []}):
        payload = {"role": "x", "regions": ["y"], "topics": [], "exclusions": [],
                   "lookback_days": 7} | bad
        assert client.put("/api/research-settings", json=payload).status_code == 422
```

- [ ] **Step 2: Run to verify failure** — 404s.

- [ ] **Step 3: Implement**

Add to `schema.sql`, and mirror it in `db.py`'s `_migrate()` (`CREATE TABLE IF NOT EXISTS`) so existing databases pick it up on startup like the other additive migrations:

```sql
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);
```

`settings.py` follows `reports.py`'s `_fetch`/`_upsert` shape (read it first). Pydantic model bounds: `lookback_days: int = Field(7, ge=1, le=90)`, `regions: list[str] = Field(min_length=1)`, `role: str = Field(min_length=1, max_length=200)`. The `PUT` handler calls `audit(conn, "user", "update_research_settings", payload, {})` inside its `get_conn()` block.

Convert `prompts/seattle-real-estate-news-reporter.md` into `prompts/market-news-reporter.md.template` with `{role}`, `{regions}`, `{topics}`, `{exclusions}`, `{lookback_days}` placeholders — read the existing prompt and preserve its output-shape instructions verbatim (the dashboard's `summary.ts` parses that shape; changing it breaks the overlay). Keep the original file in place with a one-line note pointing at the template, so K's existing cron reference doesn't dangle. `render_research_prompt()` does the substitution (str.format or manual replace — no template engine, no new dep).

Populate `DEFAULT_PACK["research"]` from the original prompt's actual content: role, the 13 named regions, the prioritized topics, the exclusions ("Do NOT summarize the national housing market unless…"), and `lookback_days: 7`.

- [ ] **Step 4: Gates** — backend suite green. **Step 5: Commit** — `feat: research settings storage, API, and templated market-news prompt`

---

### Task 6: Knowledge doc management — endpoints + dashboard panel

**Files:**
- Modify: `backend/app/routers/knowledge.py` (add upload/list/delete), `docs/CONTRACT.md`
- Create: `dashboard/src/pages/Knowledge.tsx`, route entry in `dashboard/src/App.tsx`
- Test: `backend/tests/test_knowledge_docs.py`

**Interfaces:**
- Consumes: `knowledge_dir()`, `get_corpus()`, `retrieve()` from `backend/app/knowledge/`.
- Produces: `POST /api/knowledge/docs` (`{filename, data}`, base64 — mirrors `POST /scan-card`), `GET /api/knowledge/docs` → `[{name, chunks, bytes}]`, `DELETE /api/knowledge/docs/{name}`.

- [ ] **Step 1: Write the failing tests** — the adversarial ones matter most here; this is a new filesystem write surface:

```python
import base64

def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_upload_then_listed_and_retrievable(client, tmp_knowledge):
    body = "# Widget Pricing\n\nWidgets are priced by throughput tier.\n"
    r = client.post("/api/knowledge/docs",
                    json={"filename": "widgets.md", "data": _b64(body)})
    assert r.status_code == 200
    assert "widgets.md" in [d["name"] for d in client.get("/api/knowledge/docs").json()]
    hits = client.get("/api/knowledge/search?q=widget throughput pricing tier").json()
    assert any("Widget Pricing" in h["heading"] for h in hits)


def test_traversal_filename_cannot_escape(client, tmp_knowledge):
    for evil in ("../../etc/passwd.md", "..%2f..%2fx.md", "/abs/path.md", "a/b.md"):
        r = client.post("/api/knowledge/docs", json={"filename": evil, "data": _b64("# x\n")})
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            written = r.json()["name"]
            assert "/" not in written and ".." not in written
    assert not (tmp_knowledge.parent / "passwd.md").exists()


def test_non_markdown_rejected(client, tmp_knowledge):
    assert client.post("/api/knowledge/docs",
                       json={"filename": "x.exe", "data": _b64("MZ\x00")}).status_code == 422
    assert client.post("/api/knowledge/docs",
                       json={"filename": "x.md", "data": base64.b64encode(b"\x00\x01\x02").decode()}
                       ).status_code == 422


def test_oversize_rejected(client, tmp_knowledge):
    assert client.post("/api/knowledge/docs",
                       json={"filename": "big.md", "data": _b64("#\n" + "x" * 3_000_000)}
                       ).status_code == 413


def test_delete_removes_and_deindexes(client, tmp_knowledge):
    client.post("/api/knowledge/docs", json={"filename": "temp.md",
                "data": _b64("# Zebra Facts\n\nZebras are striped.\n")})
    assert client.delete("/api/knowledge/docs/temp.md").status_code == 200
    assert client.get("/api/knowledge/search?q=zebra striped").json() == []


def test_upload_and_delete_are_audited(client, tmp_knowledge):
    client.post("/api/knowledge/docs", json={"filename": "a.md", "data": _b64("# A\n\ntext\n")})
    client.delete("/api/knowledge/docs/a.md")
    tools = [a["tool"] for a in client.get("/api/audit?limit=50").json()]
    assert "upload_knowledge_doc" in tools and "delete_knowledge_doc" in tools
```

Add a `tmp_knowledge` fixture pointing `KNOWLEDGE_DIR` at `tmp_path` (follow `test_knowledge.py`'s existing fixture style) so no test writes into the real `docs/knowledge/`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Filename safety: take `Path(filename).name` only, slugify to `[a-z0-9._-]`, force a `.md` suffix, reject if the result is empty or starts with a dot. Content: decode base64, reject >2 MB decoded (413), reject if the bytes don't decode as UTF-8 or contain NUL (422). Write, then `audit(...)` — the write and the audit go in one `get_conn()` block, and **the file write happens outside it** (Task 1 invariant: no I/O inside a transaction). Delete resolves the same slug and refuses anything not inside `knowledge_dir()` after `.resolve()`. The index self-invalidates via the existing mtime signature — no cache-busting call needed, but assert that in a test rather than assuming.

- [ ] **Step 4: Dashboard panel.** `dashboard/src/pages/Knowledge.tsx`, routed at `/knowledge` and linked in the nav: a file picker (accept `.md`) that base64-encodes and POSTs, a table of indexed docs with chunk counts and a delete button per row, and a search box hitting `GET /api/knowledge/search` that shows heading + score so the user can confirm their doc is retrievable. Reuse the existing toast + `ApiError` patterns; follow the styling of an existing page (read `Inbox.tsx`).

- [ ] **Step 5: Gates** — backend green; `npx tsc -b && npm run build` green. Add the three endpoints to `docs/CONTRACT.md` §2 with their status codes.
- [ ] **Step 6: Commit** — `feat: knowledge doc upload, list, delete + management panel`

---

### Task 7: Research settings panel

**Files:**
- Create: `dashboard/src/components/ResearchSettings.tsx`
- Modify: `dashboard/src/components/DailySummaryOverlay.tsx` (entry point), `dashboard/src/api.ts` (two methods)

**Interfaces:**
- Consumes: `GET`/`PUT /api/research-settings` from Task 5.

- [ ] **Step 1: Add the API methods** to `api.ts`:

```ts
researchSettings: <T>() => req<T>('/research-settings'),
saveResearchSettings: <T>(payload: T) =>
  req<T>('/research-settings', { method: 'PUT', body: JSON.stringify(payload) }),
```

- [ ] **Step 2: Build the panel.** Fields: role (text), regions / topics / exclusions (each a list editor — chips with add/remove, or a textarea one-per-line; pick the simpler one and stay consistent), lookback_days (number, 1–90). Below them, the `rendered_prompt` from the API shown read-only in a monospace block so the operator sees exactly what the agent will be asked. Save button with a success/error toast, mirroring the existing patterns in `Lead.tsx`.

- [ ] **Step 3: Wire the entry point.** Add a small "Adjust research keywords" affordance in `DailySummaryOverlay.tsx` near the market-watch section — that is where the operator notices the research is off-target. Opening it must not close the overlay's own state or interfere with the once-per-day auto-open logic (read the component first; it has a `sessionStorage`/date-key guard).

- [ ] **Step 4: Gates** — `npx tsc -b && npm run build` green; backend suite unchanged and green.
- [ ] **Step 5: Commit** — `feat: research settings panel with live prompt preview`

---

### Task 8: Three example vertical packs

**Files:**
- Create: `verticals/{b2b-saas,insurance,recruiting}/pack.json` and `verticals/*/knowledge/*.md`
- Test: `backend/tests/test_vertical.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
import pytest

@pytest.mark.parametrize("name", ["real-estate", "b2b-saas", "insurance", "recruiting"])
def test_every_shipped_pack_loads_cleanly(name, monkeypatch):
    """A shipped pack that silently falls back to defaults is a broken pack."""
    from app import vertical
    monkeypatch.delenv("VERTICALS_DIR", raising=False)
    monkeypatch.setenv("VERTICAL", name)
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["name"] == name
    assert len(pack["stages"]) >= 4
    assert pack["research"]["regions"]
    if name != "real-estate":
        assert pack["copy"]["booking.cta"] != "Book a tour"   # genuinely re-skinned


@pytest.mark.parametrize("name", ["b2b-saas", "insurance", "recruiting"])
def test_example_knowledge_docs_are_retrievable(name, monkeypatch, tmp_path):
    """Each example doc must actually retrieve — a sample that returns nothing
    doesn't demonstrate anything."""
    from pathlib import Path
    from app import knowledge
    d = Path(__file__).resolve().parents[2] / "verticals" / name / "knowledge"
    monkeypatch.setenv("KNOWLEDGE_DIR", str(d))
    knowledge.clear_cache() if hasattr(knowledge, "clear_cache") else None
    corpus = knowledge.get_corpus(d)
    assert len(corpus.chunks) >= 5
```

(Adjust the corpus accessor to whatever `backend/app/knowledge/index.py` actually exposes — read it first; `get_corpus(directory)` exists as of 2026-07-28.)

- [ ] **Step 2: Write the packs.** Each `pack.json` re-skins stages, labels, personas, copy, and research scope for its vertical. Suggested stage sets — adapt if the rule vocabulary can't express one, and say so:
  - **b2b-saas**: New leads → Contacted → Qualified → Demo booked → Proposal sent → Closed won. Labels: budget→"Deal size", area→"Territory", timeline→"Timeline", intent→"Deal type".
  - **insurance**: New leads → Contacted → Qualified → Quote requested → Quote delivered → Bound. Labels: budget→"Premium", area→"Region", timeline→"Renewal date", intent→"Coverage type".
  - **recruiting**: New candidates → Contacted → Screened → Onsite scheduled → Offer extended → Placed. Labels: budget→"Comp target", area→"Location", timeline→"Availability", intent→"Role type".
  Every `copy.*` key present in the real-estate pack must be present in each (the Task 3 coverage test only checks the default pack — add a parametrized version covering all shipped packs so a missing key can't slip through).

- [ ] **Step 3: Write the knowledge docs.** ~800–1,200 words each, structured with `##`/`###` headings (the index chunks on headings, so flat prose retrieves poorly). Each file opens with a visible header:

```markdown
> **Illustrative sample.** This document was written to demonstrate the structure
> of a knowledge pack and to exercise retrieval. It is not researched industry
> guidance — replace it with your own material before relying on it.
```

Topics that give retrieval something to bite on: b2b-saas → procurement/security review/deal desk/multi-year discounting; insurance → carrier appetite/renewal timing/loss runs/E&S placement; recruiting → comp banding/counteroffer dynamics/notice periods/equity refreshers.

- [ ] **Step 4: Verify retrieval per pack.** For each, point `KNOWLEDGE_DIR` at its directory and run 3 domain queries plus 2 CRM-chatter queries; paste the results into your report. Domain queries must hit; chatter must return nothing.
- [ ] **Step 5: Gates + commit** — `feat: example vertical packs for b2b-saas, insurance, and recruiting`

---

### Task 9: `docs/VERTICALS.md` and doc updates

**Files:**
- Create: `docs/VERTICALS.md`
- Modify: `README.md` (docs index + a line in the pitch section), `docs/LOCAL-AI.md` (knowledge section cross-ref), `.env.example`

- [ ] **Step 1: Write `docs/VERTICALS.md`** for someone who cloned the repo and wants their own industry. Sections: what a pack is and where it lives; the `pack.json` reference (every key, with the stage-rule vocabulary table and a worked example); how to write a knowledge doc that retrieves well (heading structure matters — the index chunks on headings; put the specific term in the heading); how to upload docs from the UI; how to tune daily research keywords from the settings panel; how to select a pack (`VERTICAL` env); and an honest "what's still real-estate-shaped if you go deeper" section naming the schema columns (`budget`/`area`/`timeline`/`intent` keep those names in the DB and API — only their labels change) and the seed data.
- [ ] **Step 2: Cross-link.** README docs index entry; one line in the README explaining the product adapts to other verticals with a pointer; `docs/LOCAL-AI.md`'s knowledge section pointing at VERTICALS.md for the pack story.
- [ ] **Step 3: Verify** every relative link resolves (same check used in earlier doc tasks) and that no instruction contradicts shipped behavior — re-read `.env.example` and `CONTRIBUTING.md` before writing.
- [ ] **Step 4: Commit** — `docs: VERTICALS.md — adapt the CRM to any industry`

---

## Self-Review (done at write time)

- **Spec coverage:** pack definition/loader → T1; serving + dashboard resolution → T2; copy/labels/personas → T3; mock briefing generator → T3b; funnel stages+rules → T4; derived funnel sections → T4b; research scope config, templated prompt, storage/API → T5; knowledge upload/list/delete + panel → T6; research settings UI → T7; three example packs with knowledge → T8; `docs/VERTICALS.md` + cross-links → T9. Non-goals (runtime switching, schema renames, PDF ingestion, marketplace) are excluded throughout. Testing strategy (real-estate no-op, pack-loading degradation, adversarial upload tests, 12 locked queries untouched) is distributed across T1/T3/T6/T8.
- **Placeholders:** the `DEFAULT_PACK` body in T1 and `BUILT_IN` in T2 are deliberately marked "transcribe from the live code" rather than invented — the shipped values are the source of truth and a fabricated list here would be wrong. Every other step carries real content.
- **Type consistency:** `load_pack()`/`clear_cache()`/`DEFAULT_PACK`/`KNOWN_RULE_TYPES` (T1) are consumed by T2/T4/T5/T8 under those exact names; `pack()`/`copy()`/`loadVertical()`/`Pack`/`Stage` (T2) by T3/T4; `render_research_prompt()` (T5) by T7 via the API; the T3 copy-coverage test is extended in T8 to cover all shipped packs.
