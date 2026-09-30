# Implementation review decisions

These decisions were made within the authorized isolated implementation. Each
records its reason and cost so it can be reviewed or reversed. Live acceptance
remains pending; no decision here authorizes merging or deployment.

- Validate proposal gateway configuration before reserving a new request; a known local configuration failure must not create an uncertain request. Existing IDs still return durable status without checking gateway availability — preserves replay recovery — cost if wrong: a user must fix setup and initiate the still-unstarted request again.

- Preserve the isolated git worktree and plan review artifacts for offline handoff; there is no merge authorization — keeps evidence reviewable — cost if wrong: extra local disk usage.

- Convert unbound running requests to unknown on startup and observed cancellation; never redispatch and never overwrite an attached proposal — preflight found a reservation can outlive its process — cost if wrong: a concurrently active request may temporarily be labelled uncertain, but can still settle normally.

- Require pending_id UNIQUE/FK plus conditional eligible-row settlement, and exact ID/session matching at each boundary — makes the intended single durable proposal invariant explicit — cost if wrong: additional validation may reject a nonconforming gateway session; WSL acceptance will expose that without writes.

- Disable environment proxy use for both native gateway HTTP clients, with a minimal read-client change assigned to Task 2 — loopback URLs alone do not prevent httpx from using an ambient external proxy — cost if wrong: an unusual proxy-dependent local setup must connect directly instead.

- Add explicit human Close request before permitting a new request after permanent uncertainty — otherwise ID-only recovery can trap the UI indefinitely — cost if wrong: an in-flight result is intentionally rejected after closure and the human must start a fresh request; gateway computation may still finish.

- Represent closing an absent ID as an empty-message failed tombstone in the existing request table; recognize it before replay text comparison without admitting any later body — preserves ID-only recovery without a second table/column — cost if wrong: the internal message-length invariant now has one explicit tombstone exception; received requests still enforce exact text and all external input remains validated.

- Refuse the legacy setup helper when an agent capability token is configured, before any CLI/file mutation — it otherwise copies the now-human token into OpenClaw and defeats separation when README recovery instructions are followed — cost if wrong: capability users must use the native setup guide; legacy setup without the agent token remains available.
