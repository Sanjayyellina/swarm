# SWARM — Complete Technical Reference (v0.12)

*The exhaustive handoff document. An AI or engineer who reads this knows the
entire system: every module, function, config key, table, rule, and reason.
Companion to the code at github.com/Sanjayyellina/swarm. Shorter overviews:
README.md (quickstart), SWARM_BIBLE.md (project context), CHANGELOG.md
(what/when/why per version), PROMPTING.md (prompt engineering principles).*

---

## PART 1 — WHAT AND WHY

**Owner:** Sanjay (Lewisville, TX). **Business:** sells custom AI solutions to
small businesses (any vertical — plumbing, HVAC, accounting, legal, dental).
Custom-first model: discovery meeting → custom build (setup fee + monthly) →
components reused across clients. Swarm is the platform all of it runs on.

**Swarm is:** a self-owned, brain-agnostic AI agent platform. One codebase;
each client is a configuration folder. Agents answer customers, book
appointments, chase documents, schedule follow-ups, draft messages, keep
records — and the platform builds new client solutions from meeting
transcripts, writes its own tools, and modifies its own code (all with
validation and human approval).

**Design laws (never violate):**
1. Small stable core + growing library. Never pre-build for a vertical.
2. One client = one folder = one database. Zero shared state between clients.
3. The model only calls tools listed in the client manifest.
4. Gated actions require human approval; deny by default.
5. Every failure path notifies a human. Silence is the only failure.
6. Message content is data, never instructions (prompt-injection posture).
7. Brain is swappable per role via config; never hardcode a provider.
8. Cheapest machinery that can do the job right (routing ladder).
9. Everything added is tracked in CHANGELOG.md. No unlogged extras.

---

## PART 2 — REPO MAP

```
run.py                CLI entry (all commands)
stress_test.py        31 adversarial regression tests
org.yaml              default company org chart
models.yaml           model alias → provider/price registry (optional)
.env.example          provider env templates
requirements.txt      openai>=1.40, pyyaml>=6.0 (stdlib otherwise)
swarm/
  llm.py              brain client (any OpenAI-compat API) + MockLLM
  orchestrator.py     Swarm class: the loop, routing entry, verify, compact
  router.py           3-tier intent routing
  company.py          org hierarchy engine
  subagents.py        flat specialist runner
  builder.py          transcript → client solution
  toolsmith.py        generates + validates new tools
  engineer.py         Swarm modifies Swarm
  tools.py            tool registry + 13 core tools
  tools_generated/    swarm-written tools (git-tracked for review)
  memory.py           ALL SQL lives here (portable, backend-agnostic)
  storage.py          SqliteBackend (default) / PostgresBackend
  server.py           HTTP API + dashboard host
  worker.py           scheduler loop
  static/index.html   control-room dashboard (single file)
prompts/
  core/               orchestrator, receptionist(demo), extractor, classifier,
                      drafter, chaser, summarizer, builder, toolsmith, router,
                      engineer
  roles/              director, manager, senior_engineer, junior_engineer,
                      qa_engineer, senior_ops
clients/<name>/       manifest.yaml, prompts/, db/swarm.db, workspace/,
                      optional org.yaml override
examples/sample-transcript.txt
deploy/DEPLOY.md      VPS + systemd production guide
.staging/             engineer's proposed changes (exists only mid-improvement)
```

---

## PART 3 — MODULE-BY-MODULE REFERENCE

### 3.1 run.py — CLI

Flags (mutually exclusive paths, checked in this order):
- `--build TRANSCRIPT --name X` → builder.build_client; prints BUILD_REPORT.
- `--apply` → engineer.apply_staged (copies .staging/ over live, deletes it).
- `--improve "REQUEST"` → engineer.improve; prints staged files + validation
  verdict + report.
- `--status` (needs --client) → owner digest: bookings, pending approvals,
  recent messages, pending jobs, LLM cost ($ + tokens).
- `--company "OBJECTIVE" [--role R]` → company.run_company (full org, or
  sub-team entered at role R).
- `--serve PORT` → server.serve (API + dashboard).
- `--work` / `--work-once` → worker loop / single pass.
- `--test` → deterministic 4-message self-test (mock brain, fresh DB):
  proves collect-details-then-book, message-taking, and chase-scheduling;
  asserts booking has real name+phone and ≥1 job scheduled.
- default (--client only) → interactive chat via swarm.dispatch() with
  cli_approve as the gate approver (y/N prompt).

### 3.2 swarm/llm.py — the brain

- `LLMResponse`: `.content` (str|None), `.tool_calls` (list of
  {id, name, arguments:dict}).
- `_load_registry()`: reads models.yaml at repo root; {} if absent.
- `_env_val(v)`: "env:VAR" → os.environ[VAR]; else passthrough.
- `class LLM`:
  - `__init__`: default OpenAI-compat client from SWARM_BASE_URL/
    SWARM_API_KEY/SWARM_MODEL; loads registry; `usage` dict
    {calls, prompt_tokens, completion_tokens, cost_usd}.
  - `_resolve(model)` → (client, real_model, price_in, price_out). Alias in
    registry → dedicated cached client (keyed by base_url+api_key); unknown
    string → default client with that model name; None → default model.
  - `chat(messages, tools=None, model=None)`: 60s timeout; 3 attempts with
    1.5^n backoff on any exception; accumulates usage incl. cost
    (tokens × price / 1e6); parses tool_calls JSON (bad JSON → {}).
- `class MockLLM`: deterministic offline brain for tests/demos. Dispatch
  order inside `chat`:
  1. system starts "# ENGINEER" → `_engineer_step` (scripted: list_files →
     write_staged IMPROVEMENT.md → run_validation → report).
  2. system starts "# ROLE:" → `_role_step` (leaders with an assign tool and
     no results yet assign to their first report; with results they return
     "<ROLE> reviewed and approved: …"; ICs return "<ROLE> completed: …").
  3. system starts "# ROUTER" → keyword triage: program/overhaul/audit/
     templates/document the → company; remind/chase/invoice/schedule a/note
     that → team:senior_ops; else direct.
  4. last message is a tool result → respond to it: available_slots →
     book_appointment (using _extract'd name/phone); booked → confirmation
     text; message_id → "passed to the team"; scheduled → chase confirmation.
  5. "Distill this conversation" prompt → empty (compaction test hygiene).
  6. system starts "# TOOLSMITH" → canned safe-stub tool code for the
     requested name.
  7. system starts "# BUILDER" → canned valid BUILD_PLAN JSON (Mock Plumbing
     Co) incl. missing_tools:[quickbooks_invoice].
  8. fresh user message → booking intent without name+phone asks for them;
     with them → check_availability; message intent → take_message;
     bank statement/missing document → schedule_task; else greeting.
  - `_extract`: regex name ("name is|this is|i'm X") and phone (7+ phone
    chars) from all user messages; service keyword match.
- `get_llm()`: SWARM_MOCK=1 → MockLLM else LLM.

### 3.3 swarm/orchestrator.py — the core loop

`class Swarm(client_dir, approve_fn=None)`:
- `__init__`: loads generated tools (toolsmith.load_generated), reads
  manifest.yaml, `_validate_manifest` (fail-fast: agent section, prompt
  file exists, non-empty tools; version default 1), Memory, get_llm(),
  approver = approve_fn or `_deny_and_queue` (SECURE DEFAULT: deny + queue
  "APPROVAL NEEDED…" message for owner), renders agent prompt with
  {{business vars}}.
- `dispatch(msg, session)`: the smart entry. manifest agent.routing != auto →
  handle(). Else router.route(); logs verdict to events (tool='route');
  company/team verdicts → company.run_company(entry_role=role|None); its
  result is history-logged and returned; org failure or FileNotFoundError
  (no org.yaml) falls back to handle(). Always answers.
- `handle(msg, session)`: cost wrapper — snapshots llm.usage, calls
  `_handle_inner`, finally logs the usage delta to events (actor='llm',
  tool='usage').
- `_handle_inner`: input capped 8000 chars; ctx = {memory, manifest,
  client_dir, session, root_dir, llm}; `_compact(session)`;
  system prompt + "[KNOWN FACTS…]" from memory.recall(session);
  history (last 20) + user msg; loop MAX_STEPS=8:
  - llm.chat in try/except → on exception: owner message "LLM ERROR…",
    graceful reply, return (outage never loses a request).
  - no tool_calls → final reply; `_verify` ONLY if tools were used this turn
    (efficiency); history-log; return.
  - tool_calls → append assistant turn; per call: not in manifest allowed →
    error; in gates and approver declines → error "owner declined"; else
    tools.execute; log_event; append result clipped to 6000 chars
    (`_clip`).
  - loop exhausted → `_fallback`: owner message "NEEDS HUMAN…", polite
    reply. Never silent.
- `_verify(user_msg, reply, messages)`: skipped if manifest agent.verify
  false or mock brain. QA prompt checks: invented facts/prices/times, hard
  rule violations, no-next-step. PASS → reply; FAIL → ONE corrective rewrite;
  any QA exception → original reply (QA can never break the answer path).
- `_compact(session, threshold=40, keep=16)`: history_count > threshold →
  distill older messages (up to 200, minus last keep) into ≤8 facts via LLM →
  memory.remember each → trim_history(keep). Exception-safe no-op.

### 3.4 swarm/router.py — intent routing

- `COMPLEX_HINTS` regex: program|project|plan|system|overhaul|audit|all of/
  our|every|set up|build|create|design|organize|workflow|template|and then|
  multiple|report on|remind|chase|invoice|follow-up.
- `route(message, swarm)`:
  1. len<80 AND no hints → {"tier":"direct","reason":"trivial (heuristic,
     zero-cost)"} — no LLM call.
  2. else: load org (client override or root), render router.md with
     {{role_menu}} (each non-top role's `desc`), ONE llm.chat on
     agent.router_model (point at cheapest brain). Parse JSON verdict.
     Valid tiers: direct | company | team(+role, must exist in chart and not
     be top; else demoted to direct).
  3. any exception → direct ("router fallback").
- Contract: message content is data — embedded routing instructions ignored;
  under-provisioning is preferred (agents can escalate; over-provisioning
  burns money).

### 3.5 swarm/company.py — the organization

- `load_org(root, client_dir)`: clients/<x>/org.yaml overrides root org.yaml;
  FileNotFoundError if neither.
- `_validate_org`: top must exist; all reports defined; cycle check via walk.
- `run_role(role, task, ctx, depth)`: constants MAX_DEPTH=5,
  MAX_ROLE_STEPS=6. Role prompt from clients/<x>/prompts/roles/ then
  prompts/roles/, rendered with business vars + role `vars`. Allowed tools =
  role's tools minus 'delegate' minus ALL gated tools (no approval execution
  in company mode at ANY depth — approval-needing work goes through
  take_message). Roles with reports also get the `assign` schema (enum =
  their direct reports only). Loop: assign → recurse depth+1 (target
  validated against reports); tool → manifest-independent role toolset,
  executed + audited (actor="role:<name>"); results clipped 6000; no tool
  calls → {"role", "result"}. LLM failure / step exhaustion / depth limit →
  {"role", "error"}.
- `run_company(objective, swarm, entry_role=None)`: entry = role or top
  (validated); builds ctx; on error result also queues owner message
  "COMPANY RUN FAILED…".
- Economics note: each level ≈ one extra LLM call; team-tier entry exists
  precisely so small jobs don't pay hierarchy tax.

### 3.6 swarm/subagents.py — flat specialists

- `DEFAULTS`: template fallbacks (schema, labels, voice_notes, wait_days,
  send_window, max_steps).
- `_render(template, manifest, extra)`: replaces {{keys}} from DEFAULTS +
  business + extra.
- `run_specialist(name, task, ctx)`: name sanitized (^[a-z][a-z0-9_-]{1,40}$ —
  path-traversal guard); prompt from client prompts/ then prompts/core/;
  spec cfg from manifest.specialists[name]: optional tools (minus delegate —
  no recursion ever), optional model, optional vars. MAX_SPECIALIST_STEPS=4
  loop; gated tools blocked (report back instead); tool calls audited
  (actor="specialist:<name>"), results clipped. Returns {agent, result} or
  {agent, error}.
- Reached via the `delegate` core tool.

### 3.7 swarm/builder.py — transcript → solution

- `REQUIRED_KEYS` = business, agent, prompts, first_build.
- `_check_plan`: keys present; agent.tools non-empty; prompts non-empty;
  agent.prompt filename must exist in prompts dict.
- `_plan_with_retry(llm, system, transcript, attempts=3)`: invalid JSON or
  failed check → error fed back verbatim, regenerate.
- `_critique`: skipped on mock. Re-reads transcript vs plan for (1) invented
  facts, (2) missed owner requirements (esp. gating requests), (3) tools
  that don't exist. "SAME" or corrected plan; exception → keep valid plan.
- `build_client(transcript_path, name, root)`: plan → clients/<name>/
  manifest.yaml (business facts filtered to non-null) + prompts/*.md
  (basename-sanitized) → missing_tools each forged via toolsmith.build_tool;
  installed ones appended to the client's agent.tools; manifest rewritten →
  BUILD_REPORT.md (first build + why, tools forged/rejected, open questions
  for the owner, future phases marked DO NOT BUILD YET, test command).
  Returns (client_dir, report_text).

### 3.8 swarm/toolsmith.py — self-written tools

- Name gate: ^[a-z][a-z0-9_]{2,40}$ (identifier + filename safety).
- `FORBIDDEN` static denylist scanned BEFORE writing/executing: subprocess,
  os.system, os.popen, eval(, exec(, __import__, socket, shutil.rmtree,
  os.remove, os.unlink, pickle, ctypes.
- `build_tool(name, desc, root, llm)`: prompt=toolsmith.md; 2 attempts; each:
  strip code fences → static scan → write swarm/tools_generated/<name>.py →
  `_validate` (importlib exec; must register REGISTRY[name]); failure text
  fed back for retry; final failure → file renamed .rejected (human review).
  Returns {tool, status: installed|rejected, …}.
- `load_generated(root)`: imports every .py in tools_generated at Swarm
  init; a broken file is skipped, never fatal.
- Contract for generated code (enforced by prompt + scan): stdlib only,
  @tool decorator, ctx-based access, secrets via os.environ with
  needs_setup status, side-effect-dangerous jobs are stubs that queue
  owner messages.

### 3.9 swarm/engineer.py — Swarm builds Swarm

- Tools exposed to the model: list_files (tree of .py/.md/.yaml/.txt,
  excluding .git/__pycache__/.staging/db/workspace/tools_generated;
  .env always forbidden), read_source (path-escape-checked, 60K cap),
  write_staged (ONLY writes under .staging/), run_validation.
- `_run_validation(root)`: copy repo → /tmp/swarm_validate (ignoring
  volatile dirs) → overlay .staging files → run `run.py --client demo-hvac
  --test` AND `stress_test.py` there with SWARM_MOCK=1 and isolated DB →
  {"passed": all green, "output": tails}. 180s timeout each.
- `improve(request, root)`: MAX_ENGINEER_STEPS=20 agentic loop with
  engineer.md; ends when the model answers without tool calls (its report).
  Returns {staged_files, validated, report}. NEVER touches live code.
- `apply_staged(root)`: copies .staging → live, removes .staging; returns
  list. Human-invoked only (`--apply`).

### 3.10 swarm/tools.py — the registry + 13 core tools

Mechanics: `@tool(name, description, parameters)` registers fn + OpenAI
function schema in global REGISTRY. `schemas_for(names)` filters.
`execute(name, args, ctx)` → JSON string; unknown tool or exception →
{"error": …} (tool failures go back to the model, never crash the loop).

| Tool | Params | Behavior/guards |
|---|---|---|
| current_time | – | business-local via `_now` (manifest business.timezone, zoneinfo; fallback server time) |
| check_availability | day ("today"/"tomorrow"/YYYY-MM-DD) | slots from business.slot_hours (default 9-16) minus taken; past slots filtered vs business-local now; ≤5 returned; note when none |
| book_appointment | name, phone, start, service | IDEMPOTENT: same phone+start returns existing id ("already booked") |
| cancel_appointment | appointment_id | status → cancelled (typically gated) |
| take_message | name, phone, body | the universal escape hatch; also used for EMERGENCY/APPROVAL NEEDED/NEEDS HUMAN system messages |
| list_appointments | – | booked, ordered by start |
| remember_fact | fact | notes table per session; injected as [KNOWN FACTS] forever after |
| schedule_task | minutes_from_now, task | jobs table; task text must be self-contained; executed later by worker |
| http_request | method(GET/POST), url, json_body? | scheme http/https only; host must match manifest integrations.allowed_domains (exact or subdomain); redirects BLOCKED (SSRF); 15s timeout; 20K body cap; HTTP errors returned as status+body |
| save_file | filename, content | basename-only (no traversal) into clients/<x>/workspace/ |
| read_file | filename | same sandbox; 50K cap; miss → error + available list |
| delegate | agent, task | runs subagents.run_specialist; task must be self-contained |
| (generated) | per tool | e.g. quickbooks_invoice stub from the demo build |

### 3.11 swarm/memory.py + storage.py — data layer

ALL SQL lives in memory.py, written portably ('?' placeholders). Backends
implement query/execute/insert (insert returns new id). `SWARM_DB_URL`
unset → SqliteBackend: file clients/<x>/db/swarm.db (or
$SWARM_DB_DIR/<client>/swarm.db), check_same_thread=False + a write lock;
postgres://… → PostgresBackend: psycopg2, '?'→'%s', SERIAL/DOUBLE
PRECISION schema, INSERT … RETURNING id, autocommit, RealDictCursor.
(Postgres path written but not yet exercised against a live server — run
stress suite against one before first production use.)

Tables (identical logical schema both backends) + indexes:
- appointments(id, name, phone, start, service, status='booked', created)
  idx(phone,start,status)
- messages(id, name, phone, body, created)
- history(id, session, role, content, created) idx(session,id)
- events(id, actor, tool, args≤500, result≤500, created) — the audit trail;
  actors: orchestrator, specialist:<n>, role:<n>, router, scheduler, llm
- jobs(id, run_at, task, session, status pending|done|failed, created)
  idx(status,run_at)
- notes(id, session, fact≤500, created) idx(session,id) — long-term memory

Memory methods: add_history/get_history(20)/history_count/trim_history;
book/find_booking/appointments/cancel; take_message/recent_messages;
remember/recall(15); schedule_job/due_jobs/pending_jobs_count/recent_jobs/
finish_job; log_event/recent_events(60)/last_route/event_count/usage_turns;
generic read-only `query()` for reporting. `memory.conn` exposed only on
sqlite for legacy tests; engine code uses methods exclusively.

### 3.12 swarm/server.py — API + dashboard

serve(swarm, port): stdlib ThreadingHTTPServer.
- Global per-instance: handle_lock (ONE request at a time — SQLite
  consistency + conversation ordering; scale-out = instance per client),
  rate window (SWARM_RATE_LIMIT/min, default 60 → 429), token auth
  (SWARM_SERVER_TOKEN set → "Authorization: Bearer <t>" required on
  POST /handle and GET /api/*; unset = open dev mode).
- GET /health → {ok, client} (no auth).
- GET / → swarm/static/index.html.
- GET /api/state → client, model, appointments(25), approvals (messages
  starting "APPROVAL NEEDED"), other messages, jobs(25), llm_turns,
  event_count.
- GET /api/events → last 60 audit rows.
- POST /handle {message, session?, channel?}: 100KB body cap (413);
  malformed JSON → clean 4xx/5xx; channel (≤16 chars, default web) namespaces
  session as "<channel>:<session≤48>"; dispatch(); response {reply, session,
  route: last routing verdict}. Handler exceptions → 500 JSON (never hangs).

### 3.13 swarm/worker.py — proactive work

- `run_due_jobs(swarm)`: each due job → swarm.handle("[SCHEDULED TASK —
  execute now, do not reschedule unless instructed] <task>", session=job's);
  audited (actor=scheduler); done. Exception → job failed + owner message
  "SCHEDULED JOB n FAILED…" (one bad job never stops the rest).
- `work_loop(swarm, interval=30)`: poll forever. CLI: --work / --work-once.

### 3.14 dashboard (swarm/static/index.html)

Single file, no frameworks. Dark control-room theme. Panels: chat (center;
POST /handle; per-reply tier badge parsed from route verdict:
direct=green/team=blue/company=amber), operations (left; approvals
highlighted, appointments, messages, jobs), audit trail (right; live, route
and assign rows color-coded), header stats (model, turns, events). 4s
polling. 401 → prompts once for token → localStorage. Session id persisted
in localStorage ("ui-…").

---

## PART 4 — CONFIGURATION REFERENCE

Environment variables:
| Var | Meaning | Default |
|---|---|---|
| SWARM_BASE_URL | default provider endpoint | https://api.openai.com/v1 |
| SWARM_API_KEY | default provider key | "" |
| SWARM_MODEL | default model | gpt-4o-mini |
| SWARM_MOCK | 1 = deterministic mock brain | off |
| SWARM_DB_URL | postgres://… switches backend | sqlite |
| SWARM_DB_DIR | relocate sqlite files | client folder |
| SWARM_SERVER_TOKEN | API/dashboard bearer token (REQUIRED in prod) | open |
| SWARM_RATE_LIMIT | requests/min | 60 |

models.yaml: `default: {price_in, price_out}` + `aliases: {name: {base_url,
api_key: "env:VAR", model, price_in, price_out}}` (USD per 1M tokens).
Aliases usable anywhere a model is named: manifest agent.router_model,
specialists.<n>.model, org.yaml roles.<r>.model.

manifest.yaml (per client) — full key reference:
```yaml
version: 1                    # schema stamp (absent = 1)
name: <display name>
agent:
  prompt: <file in prompts/>  # required, must exist
  verify: true|false          # QA pass on tool-using turns
  routing: auto|direct        # direct disables the router
  router_model: <alias|name>  # triage brain (make it cheap)
  tools: [ ... ]              # REQUIRED non-empty; the agent's whole world
gates: [ ... ]                # tools requiring owner approval
specialists:                  # delegate targets
  <name>: {model: …, tools: […], vars: {…}}   # all optional
integrations:
  allowed_domains: [api.example.com]          # http_request allowlist
business:                     # injected into prompts as {{vars}}
  business_name / owner_name / services / hours / service_area
  timezone: America/Chicago   # drives current_time + slot math
  slot_hours: [9,10,11,13,14,15,16]
```

org.yaml: `top: <role>` + `roles: {name: {prompt, model, reports: […],
tools: […], desc: <router menu line>, vars: {…}}}`. Per-client override:
clients/<x>/org.yaml. Validated: top exists, reports defined, no cycles.

---

## PART 5 — SECURITY MODEL (complete)

- Gates deny by default; approval only via orchestrator approve_fn (CLI y/N
  today; SMS approval is a planned seam). Company mode: gated tools never
  executable at any depth. Specialists: never gated tools, never delegate.
- Path traversal: sanitized at specialist names, toolsmith names, manifest
  prompt (basename), save/read_file (basename), engineer read/write
  (realpath containment), builder prompt filenames (basename).
- SSRF: http_request scheme whitelist, per-client domain allowlist,
  redirects blocked, timeouts, body caps.
- Injection: parameterized SQL everywhere; prompt-injection posture =
  content-is-data rule in router/prompts + gates/allowlists as backstops
  (LLM reasoning can still be influenced — residual risk, mitigated not
  eliminated).
- Generated code: static denylist scan before any execution; validation
  import in-process (accepted risk — code lands in git for review; stubs
  until credentials provided); rejects quarantined as .rejected.
- API: bearer token, rate limit, 100KB cap, serialized handling (fixed the
  concurrent-500s bug), LLM outage → graceful reply + owner note.
- Reliability: LLM 3-attempt retry w/ backoff; step budgets everywhere
  (loop 8, specialist 4, role 6, engineer 20, org depth 5); booking
  idempotency; jobs isolated per-failure.
- Data: .env and client db/ git-ignored; audit args/results truncated at
  500 chars; per-client DB isolation.

Validated by stress_test.py (31 checks, 8 categories): gates, traversal ×5,
SSRF ×4, recursion/delegation, garbage tool args ×5, injection strings
through the full loop ×6 + table integrity, API malformed/auth/concurrency,
scheduler edge cases. Run before EVERY release: `SWARM_MOCK=1 python
stress_test.py`. Plus `--test` (behavioral self-test) and inline feature
tests per version.

---

## PART 6 — EXTENSION RECIPES

**New client (manual):** copy clients/demo-hvac → edit manifest (facts,
tools, gates) → rewrite prompts from discovery notes → `--client X` to test.
**New client (auto):** transcript → `--build meeting.txt --name X` → review
BUILD_REPORT + generated code → test → deploy.
**New tool:** function + @tool schema in tools.py (or let the Toolsmith
write it); add name to a manifest; gate it if it spends/sends/deletes.
**New specialist:** prompt in prompts/core/<name>.md (follow the skeleton in
PROMPTING.md) + entry under manifest specialists.
**New role:** prompt in prompts/roles/ + org.yaml entry (reports, tools,
desc for the router menu).
**New channel:** any adapter that POSTs /handle with {message, session,
channel}. Nothing in core changes.
**New provider:** alias in models.yaml. **Scale DB:** SWARM_DB_URL.
**Change Swarm itself:** `--improve "…"` → review .staging → `--apply`
(or hand-edit; then run both test suites).
**Production:** deploy/DEPLOY.md (VPS, systemd api+worker, .env with
SERVER_TOKEN, HTTPS proxy, nightly db file backup).

---

## PART 7 — STATUS, LIMITS, HONESTY

Version: v0.12 (see CHANGELOG.md for the per-version ledger: v0.1 core →
v0.2 subagents/verify → v0.3 builder/toolsmith → v0.4 API/scheduler →
v0.5 hardening/memory → v0.6 engineer/costs/deploy → v0.7 company →
v0.8 routing → v0.9 team-tier + gated fix → v0.10 dashboard → v0.11
storage seam → v0.12 provider/channel/version seams).

**Not yet real (the shakedown list):** never run on a real LLM (mock-only so
far — first real hours WILL surface prompt/tool-format issues per provider);
no .env with a key; no real channel connected (Twilio/email are adapters to
write); no real integrations (client calendars/QuickBooks — Toolsmith makes
stubs until credentials exist); not deployed (laptop-bound until the VPS);
zero real transcripts/customers (prompt iteration hasn't started); Postgres
backend untested against live server; single-token dashboard auth;
approvals via CLI only.

**Capability envelope:** excellent for configured, repeatable business
workflows on SMB volume (serialized per-client instance). Mid-market needs:
Postgres flip, parallel handling, multi-user auth, compliance posture —
seams pre-cut, ~2-3 weeks when a deal pays for it. Swarm does NOT replace a
general frontier assistant for novel open-ended work; it replaces it per
configured domain, and its builder/engineer close the gap further with a
strong brain plugged in.

**Working docs elsewhere (owner's Cowork folder, not the repo):** market
playbook, Lewisville target list + call scripts, discovery checklist, M5
Pro local-model build spec, platform architecture spec.
