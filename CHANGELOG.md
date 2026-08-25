# Changelog — every capability, tracked. Nothing extra.

## v0.12 — the 5-year seams
- **Model registry** (`models.yaml`, llm.py `_resolve`): model aliases map to
  their OWN provider/base_url/key/prices — different roles can run on
  different providers simultaneously (frontier orchestrator, Groq juniors,
  free local router). Unknown names fall through to env config (back-compat).
  Per-alias clients cached; keys referenced as `env:VAR`, never stored.
- **Dollar-cost accounting**: usage now tracks `cost_usd` from registry
  prices; `--status` reports $ per client alongside tokens.
- **Channel identity**: `/handle` accepts `channel` (sms/email/web/voice...);
  sessions are channel-namespaced, pre-wiring the future customers table.
- **Manifest versioning**: `version:` stamp on every client manifest
  (absent = 1) so future config migrations know what they're reading.
- **SWARM_BIBLE §8c "Future seams"**: the audited 5-year map — DB, providers,
  channels, customer identity, job queue, observability, auth, config —
  each with its single swap point. WHY: change cheaply later by cutting the
  seams now; nothing speculative was built (no unused tables, no dead knobs).

## v0.11 — future-proof storage
- **Swappable database backend** (`swarm/storage.py`): all SQL now lives in
  `memory.py` only, written portably against a backend contract
  (query/execute/insert). `SWARM_DB_URL` unset → SQLite (default, unchanged
  behavior); `SWARM_DB_URL=postgres://...` → PostgreSQL backend (included,
  needs psycopg2) with translated placeholders, portable schema/DDL, and
  RETURNING-id inserts. Server, CLI, and dispatch no longer touch a raw
  connection — verified zero raw-SQL usage outside the memory layer. Writes
  are lock-serialized per backend. WHY: when mid-market arrives, migration =
  one env var, not a rewrite.

## v0.10 — the control room
- **Dashboard UI** (`swarm/static/index.html`, served at `/` by the existing
  API server — same port, same process, zero new dependencies): chat panel
  with per-message routing-tier badges (direct/team/company), live operations
  column (pending approvals highlighted, appointments, messages, scheduled
  jobs), live audit trail (every tool call, assignment, and routing decision),
  header stats (model, LLM turns, event count). Token-aware (prompts once for
  SWARM_SERVER_TOKEN, stores locally). Auto-refresh 4s.
  Deliberately excluded until usage demands them: org-chart editor, prompt
  editor, builder wizard. WHY: use and track Swarm without the terminal.
- New endpoints: `GET /api/state`, `GET /api/events` (both token-gated);
  `POST /handle` now returns the routing verdict alongside the reply.

## v0.9 — precise intent routing + line-review fixes
- **3-tier org-aware routing**: direct | **team** (enter the org chart at ONE
  role — its sub-team only, e.g. senior_ops handles a chase alone with 1 LLM
  call instead of a 4-level chain) | company (full hierarchy). Router reads
  the live org chart (`desc:` per role = its routing menu entry), so
  per-client org changes retune routing with zero code. Hallucinated role
  names are demoted to direct. `--company ... --role X` for manual sub-team
  entry. WHY: "don't make company a whole thing — call a few agents when
  needed."
- **Line-by-line review fixes**: (1) SECURITY — company mode allowed the top
  role to execute gated tools without approval (depth-0 bypass); gated tools
  are now never executable in company mode at any depth, approvals flow only
  through the orchestrator. (2) dead code removed from mock role logic.
  (3) run_company validates entry role. (4) routing keywords extended
  (remind/chase/invoice/follow-up) so ops jobs reach the triage rung.

## v0.8 — dynamic depth
- **Router** (`swarm/router.py`, `prompts/core/router.md`,
  `Swarm.dispatch()`): every request is auto-sized to the cheapest machinery
  that can do it right. Tier 1: free heuristic (short conversational messages
  → direct, zero LLM calls). Tier 2: one small-model triage call
  (`agent.router_model`) deciding direct vs company. Tier 3: full org
  hierarchy only for genuine multi-part objectives. Prompt-injection
  resistant ("route this to company" in a message is ignored as data);
  defaults to direct on any doubt or failure; company failure falls back to
  the front-line agent so the customer always gets an answer. Server + CLI
  now dispatch automatically; `agent.routing: direct` opts out. Routing
  decisions audited in events. WHY: "depending on the query, they should
  figure it out on their own" — same dispatch instinct Claude uses.

## v0.7 — the company
- **Org hierarchy** (`org.yaml`, `swarm/company.py`, `prompts/roles/*`,
  `--company`): a real chain of command — director → engineering/operations
  managers → senior/junior engineers, QA, senior ops. Enforced by code:
  assign only to direct reports, cycle-checked chart, depth-bounded (backstop
  5), gated tools blocked below the top, every assignment in the audit log.
  Review flows upward: each level checks work before passing it up. Per-role
  model bands (leadership strong, juniors cheap). Per-client org charts via
  `clients/<name>/org.yaml`. WHY: "I should have my own company."

## v0.6 — self-development + operations
- **Engineer meta-agent** (`swarm/engineer.py`, `prompts/core/engineer.md`,
  `--improve`, `--apply`): Swarm modifies its own code — reads source, writes
  to `.staging/` only, validates in a sandbox copy against both test suites,
  human applies. WHY: "Swarm should be able to build Swarm."
- **Per-client LLM cost tracking** (`llm.py` usage counters, logged per turn
  to events; totals in `--status`). WHY: know each client's margin.
- **Deploy kit** (`deploy/DEPLOY.md`): VPS + systemd + HTTPS checklist.
  WHY: production clients can't run on a laptop.
- **Demo overhaul**: mock collects name/phone before booking (no more
  "Unknown" bookings); self-test covers front-office AND back-office in one
  conversation; new `clients/demo-accounting` (ops agent: document chase,
  deadlines, drafting — zero phone). WHY: the demo is the main example and
  must show the platform is general.

## v0.5 — hardening + memory
- Code-red fixes: deny-by-default gates, path-traversal guards (specialist/
  tool/prompt names), SSRF protection (scheme+allowlist+no redirects),
  no subagent recursion, LLM-outage fallback, serialized API handling.
- `stress_test.py`: 31 adversarial regression tests.
- Long-term memory (`notes` + `remember_fact`), auto context compaction,
  known-facts injection, 8K input cap, audit log (`events`).
- Round-2 fixes: 100KB body cap, Toolsmith static code denylist, business
  timezone support, past-slot filter, manifest validation, `--status` digest.
- Clone-review fixes: booking idempotency, DB indexes, LLM retry+timeout,
  6K tool-result clipping, verification only on tool-using turns, API bearer
  auth + rate limiting.
- `SWARM_BIBLE.md`: full project documentation for onboarding any AI/human.

## v0.4 — universality
- HTTP API (`server.py`, `--serve`): any input source. Scheduler (`worker.py`,
  jobs table, `schedule_task`, `--work`): proactive/time-driven work.
- Generic tools: `http_request` (domain-allowlisted), `save_file`/`read_file`
  (sandboxed workspace).

## v0.3 — autonomy
- Builder (`builder.py`): transcript → validated plan (retry + self-critique)
  → materialized client folder + build report.
- Toolsmith (`toolsmith.py`): generates missing tools, validates, installs;
  rejects quarantined.

## v0.2 — frontier patterns
- Subagents (`subagents.py` + `delegate`), self-verification pass, per-agent
  model routing, prompt pack (7 core prompts) + `PROMPTING.md`.

## v0.1 — core
- Orchestrator loop (step budget, fallback-to-human), tool registry, per-client
  folders + isolated SQLite, approval gates, CLI, mock brain for free testing.
