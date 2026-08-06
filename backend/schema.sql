PRAGMA foreign_keys = ON;

-- Timestamp convention (all *_ts / *_at columns): ISO-8601, naive local
-- wall-clock (YYYY-MM-DDTHH:MM:SS, no `Z`, no offset) — not UTC. Every API
-- boundary normalizes to this via parse_ts() (backend) / toNaiveLocal()
-- (dashboard); aware input is converted to local, never stripped.

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  source TEXT DEFAULT 'note',           -- form | text | note | referral
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','contacted','meeting_booked','closed')),
  outcome TEXT CHECK (outcome IN ('won','lost')),
  close_reason TEXT,
  score INTEGER,                        -- 0-100, deterministic formula
  score_reason TEXT,                    -- LLM-written explanation
  budget INTEGER,                       -- dollars
  area TEXT,
  timeline TEXT,
  preferences TEXT NOT NULL DEFAULT '[]',    -- JSON array
  intent TEXT DEFAULT 'unknown',        -- buy | sell | browse | unknown
  missing_fields TEXT NOT NULL DEFAULT '[]', -- JSON array
  is_neglected INTEGER NOT NULL DEFAULT 0,
  persona TEXT,                          -- e.g. "Luxury Executive"; agent-set, nullable
  relationship_summary TEXT,             -- AI-written paragraph for the profile hero
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
  last_activity_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- activity timeline; type: note | form | text | call | merge | status_change | agent_action
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

CREATE TABLE IF NOT EXISTS appointments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL,
  location TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- weekday: 0=Monday .. 6=Sunday; times as 'HH:MM'
CREATE TABLE IF NOT EXISTS availability (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  weekday INTEGER NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL
);

-- scheduled follow-ups; the dashboard polls for due ones and surfaces them
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  due_ts TEXT NOT NULL,
  note TEXT,
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- every agent/tool action lands here; powers the dashboard activity stream
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
  actor TEXT NOT NULL,                  -- agent | user | cron
  tool TEXT NOT NULL,
  input TEXT NOT NULL DEFAULT '{}',
  output TEXT NOT NULL DEFAULT '{}',
  lead_id INTEGER REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,                   -- user | agent
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- Executive Briefing — one row per date; K's 7am cron posts, dashboard reads.
-- payload is the full JSON shape documented in docs/BRIEFING-UI.md.
CREATE TABLE IF NOT EXISTS briefing (
  date TEXT PRIMARY KEY,                 -- YYYY-MM-DD
  payload TEXT NOT NULL,
  generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- Deterministic dashboard insights — one row per date; dashboard write-through
-- caches computeInsights() output here; K's morning-summary cron reads it back.
-- payload shape documented in docs/INSIGHTS.md.
CREATE TABLE IF NOT EXISTS insights (
  date TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- Daily summary overlay (market watch + AI insights) — one row per date; agent
-- posts. payload shape documented in docs/BRIEFING-UI.md ("Daily summary overlay").
CREATE TABLE IF NOT EXISTS daily_summary (
  date TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  generated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- key/value settings, JSON payload per key. Distinct from the date-keyed
-- briefing/insights/daily_summary tables: these are operator preferences that
-- persist across days. First key: "research" (daily market-search scope,
-- editable from the dashboard — see routers/settings.py).
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
);

-- Agent-proposed CRM writes awaiting a human decision (see
-- routers/pending_changes.py). Only agent-tagged calls (X-Actor: agent) to
-- create/update/note/book/remind/close/delete/merge get queued here.
CREATE TABLE IF NOT EXISTS pending_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  operation TEXT NOT NULL,     -- one of the reviewed CRM operations
  lead_id INTEGER,             -- target lead when known; NULL for create_lead
  payload TEXT NOT NULL,       -- JSON body as submitted, replayed verbatim on approve
  summary TEXT NOT NULL,       -- human-readable one-liner for the approval dialog
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | applying (internal) | approved | denied
  result TEXT,                 -- JSON of the applied result, filled on approve
  deny_reason TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
  decided_at TEXT
);
