# OpenHouse Intelligence

OpenHouse Intelligence is a local-first CRM for real-estate agents. Add a
lead, record a voice note, write a note, schedule a follow-up, or book a tour
in plain language. Before an agent-created CRM change is saved, you review it
in **Pending approvals**.

It runs in two useful ways:

- **Demo mode** is safe for trying the product and works offline after its
  first launch installs the project dependencies.
- **Real local-AI mode** connects the dashboard to your own OpenClaw agent and
  local CRM tools.

## Project origins and team

The initial prototype was built in one day by a four-person team at the Dell × NVIDIA
BuilderBase Hackathon in Seattle, where it placed **Top 8**. Development continued after
the event; the current repository includes reliability, approval, and local-AI work beyond
the hackathon prototype.

The team combined dashboard, backend/data, and agent integration work. **Johaan Mannanal**
contributed dashboard development, frontend/API integration, demo preparation, and the
product pitch. See the [contributor history](https://github.com/tobywashere/open-house-intelligence-crm/graphs/contributors)
for the wider team's work.

The current agent workflow supports **eight approval-gated CRM actions**: creating and
updating leads, adding notes, scheduling follow-ups, booking appointments, closing leads,
merging duplicates, and deleting leads. These agent-created changes wait for human review;
they are not silently applied. See [the API and tool contract](docs/CONTRACT.md).

## Try the demo

You need Git, Python 3.11 or newer, and Node.js 20 or newer.

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173). Demo mode creates
clearly labeled sample data only in a new local database. It does not need a
model, GPU, or account. The first launch may download Python and Node packages;
after they are installed, demo mode works without internet access.

## Local-first, with explicit approval

The default local-AI setup keeps CRM records in SQLite and runs inference and
transcription on the host. The agent proposes changes; you can edit, approve, or
deny them before they are applied. Booking availability is checked again at approval.

Optional remote model providers, Gmail/Calendar integrations, Discord, and market
information workflows can send data off the host when configured. Local storage
alone is not a claim that every optional integration is offline. Do not commit
client records, recordings, API tokens, or the local database.

## Real local AI and operations

For real AI, use a tool-capable model with OpenClaw and the dedicated CRM agent.
[Setup and operations](docs/SETUP.md) covers the exact commands, live verification,
approval workflow, voice notes, optional integrations, backups, and recovery.

A reachable endpoint is not enough: the live check must verify both a chat
completion and an audited, read-only CRM tool call. The application labels
fallbacks and unavailable information rather than fabricating CRM facts.

## Documentation

- [Setup and operations](docs/SETUP.md): install, verify, operate, and troubleshoot.
- [API and tool contract](docs/CONTRACT.md): the eight approval-gated actions.
- [Local-AI reference](docs/LOCAL-AI.md): configuration and supported hosts.
- [Mac mini guide](docs/MAC-MINI-SETUP.md): Apple-silicon deployment.
- [Contributing](CONTRIBUTING.md): development and verification.

For a quick first look, start with the credential-free demo above. The current
repository includes work completed after the one-day hackathon prototype.
