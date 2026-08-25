# SWARM BIBLE — everything about this project

*Read this file and you know the whole project: what Swarm is, why every piece
exists, how it works, and where it's going. Written so any AI (Claude, GPT,
local models) or human collaborator can be fully onboarded by this one document.*

---

## 1. The vision

**Owner:** Sanjay (Lewisville, TX). Building a business selling custom AI
solutions to small businesses — law firms, accounting firms, dental, home
services, ANY vertical. Explicitly NOT a voice/receptionist product company:
each client gets a custom-built solution for whatever their business actually
needs (front-office lead capture OR back-office automation like invoicing,
document chasing, tool-syncing, reports).

**Business model:** custom-first. Meet the owner, record the discovery
conversation, build them a custom solution (setup fee $1.5K–5K + monthly
$200–800), reuse components across clients so each build gets faster. The
platform is the compounding asset.

**Swarm** is that platform: a self-owned AI agent system designed to match
frontier-assistant architecture ("a prodigy of Claude"). Key requirement: the
brain (LLM) is swappable per task — frontier API models today, local models on
an M5 Pro MacBook (48GB, ordered) later, self-hosted NVIDIA hardware when
revenue justifies it. The endgame is autonomy: transcript in → working client
solution out, with minimal human (Sanjay/Claude/GPT) involvement.

## 2. Design philosophy (why it's built this way)

- **"Ready for everything" = small stable core + growing library.** Never
  pre-build for a vertical. The core loop never changes; tools, prompts, and
  configs accumulate per client and are kept forever.
- **Custom at the surface, identical machinery underneath.** Every client is a
  folder (manifest + prompts + isolated DB). Client #8 takes days because
  clients #1–7 built the library.
- **Frontier patterns, honestly implemented:** agentic loop, subagents,
  self-verification, model routing, long-term memory, context compaction,
  audit trail, approval gates. Architecture parity with frontier assistants is
  achieved; capability parity depends on which model is plugged in.
- **Secure by default:** gates deny without an approver; specialists can't
  execute gated actions or delegate; every failure path notifies a human;
  nothing fails silently.

## 3. Architecture (the five layers)

```
CHANNELS  → HTTP API (server.py) — any source: forms, SMS gateways, email hooks, software
ORCHESTRATOR (orchestrator.py) — the loop: plan → tools → verify → respond
SPECIALISTS (subagents.py + prompts/core/) — extractor, classifier, drafter,
             chaser, summarizer; spawned via the `delegate` tool
TOOLS (tools.py + tools_generated/) — registry of typed functions; the compounding asset
MEMORY (memory.py) — per-client SQLite: history, notes (long-term), events (audit),
             jobs (scheduler), appointments, messages
```

Plus two meta-agents that make it autonomous:
- **Builder** (`builder.py` + `prompts/core/builder.md`): discovery transcript →
  validated BUILD_PLAN JSON (retry on invalid, then a self-critique pass
  against the transcript) → materialized client folder + BUILD_REPORT.md.
- **Toolsmith** (`toolsmith.py` + `prompts/core/toolsmith.md`): when the plan
  needs a nonexistent tool, generates the Python module, validates it
  (syntax/import/registration), retries once with the error fed back,
  installs to `swarm/tools_generated/` (quarantines rejects as `.rejected`).

## 4. File map

```
run.py                  CLI: --client (chat) --test --build --serve PORT --work
stress_test.py          31 adversarial tests; run before every release
PROMPTING.md            the 10 prompt-engineering principles + universal skeleton
README.md               quickstart
.env.example            provider configs: OpenAI/Groq/Anthropic/LM Studio/Ollama
swarm/
  llm.py                one client for any OpenAI-compatible brain + MockLLM (offline tests)
  orchestrator.py       loop, gates, verification (_verify), compaction (_compact), fallback
  subagents.py          specialist runner (no gated tools, no recursion, per-agent model)
  tools.py              registry + core tools (see §5)
  toolsmith.py          tool generation + validation
  builder.py            transcript → client solution
  server.py             stdlib HTTP API (serialized per client)
  worker.py             scheduler loop for time-driven jobs
  memory.py             SQLite layer
prompts/core/           orchestrator, receptionist(demo), extractor, classifier,
                        drafter, chaser, summarizer, builder, toolsmith
clients/<name>/         manifest.yaml, prompts/, db/, workspace/ (per client, isolated)
```

## 5. Core tools (as of v0.5)

current_time · check_availability · book_appointment · cancel_appointment ·
take_message · list_appointments · remember_fact (long-term memory) ·
schedule_task (future work → jobs table) · http_request (allowlisted domains
only, redirects blocked) · save_file / read_file (sandboxed to client
workspace) · delegate (spawn specialist)

## 6. Key mechanisms

- **Manifest = permissions.** The model can only call tools listed in the
  client's `manifest.yaml`. `gates:` lists tools needing owner approval
  (deny-by-default when no approver is attached, e.g. API mode — denied
  actions are queued as owner messages).
- **Verification** (`agent.verify: true`): QA pass on final replies checks
  invented facts / rule violations / dead ends; one corrective rewrite max.
- **Memory model:** last ~20 messages verbatim; beyond 40, `_compact()`
  distills the older half into durable facts (notes table) and trims. Notes
  are injected into the system prompt as "[KNOWN FACTS]". Agents can also
  explicitly `remember_fact`.
- **Model routing:** `SWARM_MODEL` env = default brain; per-specialist
  `model:` override in manifest. Intended: strong model for Builder and
  orchestration, small cheap models for narrow specialists.
- **Audit:** every tool call by anyone → `events` table (actor, tool, args,
  result). Use for debugging and prompt iteration.
- **Scheduler:** agents call `schedule_task`; `--work` loop executes due jobs
  by feeding them back through `handle()`. Failed jobs → owner message.

## 7. Security posture (from the code-red audit, all tested in stress_test.py)

Fixed and regression-tested: gate bypass in API mode (now deny-by-default),
path traversal via specialist/tool/prompt names (sanitized), SSRF (scheme +
domain allowlist + redirects blocked), subagent recursion (delegate stripped),
LLM outage handling (graceful reply + saved request), concurrent request
corruption (serialized handling). Also verified: SQLi via parameterized
queries, prompt-injection strings, 20KB inputs (capped at 8K), malformed API
requests. Known accepted risks: Toolsmith executes generated code during
validation (by design — code lands in git for review; stubs only until real
credentials are added); prompt injection can still influence LLM *reasoning*
(mitigated by gates, allowlists, and never-fabricate prompt rules).

## 8. How to run

```bash
pip install -r requirements.txt
cp .env.example .env   # choose provider block; SWARM_MOCK=1 for offline
python run.py --client demo-hvac --test        # prove machinery (no API cost)
python run.py --build examples/sample-transcript.txt --name new-client
python run.py --client new-client              # chat
python run.py --client new-client --serve 8080 # HTTP API
python run.py --client new-client --work       # scheduler
SWARM_MOCK=1 python stress_test.py             # before every release
```

Env: SWARM_BASE_URL, SWARM_API_KEY, SWARM_MODEL, SWARM_MOCK, SWARM_DB_DIR.

## 8b. The organization + dynamic routing (v0.7–v0.9)

Above the flat orchestrator sits a company: `org.yaml` defines roles
(director → engineering/operations managers → senior/junior engineers, QA,
senior ops), each with its own prompt (`prompts/roles/`), tools, model band,
and `desc` (its routing menu entry). Chain-of-command is enforced by code:
assign only to direct reports, cycle-checked chart, depth-bounded, gated
tools never executable in company mode, every assignment audited.

Every incoming request is auto-sized by the router (`swarm/router.py`,
`Swarm.dispatch()`): tier 1 free heuristic → direct; tier 2 one cheap triage
call (agent.router_model) choosing direct | team (enter the chart at one
role — a few agents, not the whole company) | company (full hierarchy).
Defaults to direct on doubt; org failures fall back to the front-line agent;
embedded routing instructions in messages are ignored as data. The Engineer
meta-agent (`--improve`) lets Swarm modify its own code: staged writes,
sandboxed validation against both test suites, human `--apply`. Per-turn LLM
cost is logged to events and summarized in `--status`. `deploy/DEPLOY.md`
covers VPS/systemd production setup. `CHANGELOG.md` tracks every capability.

## 8c. Future seams — the 5-year map

Where tomorrow's changes plug in, so nobody rewrites what was designed to swap:

- **Database** → `SWARM_DB_URL` env var (storage.py backends; SQLite default,
  Postgres included). All SQL lives in memory.py only.
- **Model providers** → `models.yaml` aliases (per-role providers + $ prices);
  unknown names fall through to SWARM_* env. Reference aliases anywhere a
  `model:` appears (manifests, org.yaml, router_model).
- **Channels** → anything POSTs `/handle` with a `channel` field; sessions are
  channel-namespaced (`sms:+1972...`). New channel = a thin adapter, never a
  core change.
- **Customer identity (not built)** → a future `customers` table linking
  channel-sessions to one person; the channel-namespaced sessions were
  designed to make that a join, not a migration.
- **Job queue** → worker polls via Memory methods only; a Redis/Celery swap
  touches memory.py + worker.py, nothing else.
- **Observability** → every action funnels through `Memory.log_event`; metrics
  export/alerting hooks in at that single point.
- **Dashboard auth (single token today)** → `_authed()` in server.py is the
  one place multi-user auth replaces it.
- **Config migrations** → every manifest carries `version:` (absent = 1).

## 9. Version history

- v0.1 core: loop, tools, memory, gates, per-client folders, CLI, mock brain
- v0.2 frontier patterns: subagents, self-verification, model routing; prompt pack + PROMPTING.md
- v0.3 autonomy: Builder (retry+critique), Toolsmith (generate+validate+install)
- v0.4 universality: HTTP API, scheduler, http_request/file tools; de-emphasized voice
- v0.5 hardening + memory: code-red fixes, 31-test suite, long-term notes,
  auto-compaction, input caps, audit logging

## 10. Roadmap (build when client work demands)

1. Owner approval over SMS/email (gates currently approve via terminal only)
2. Deploy script: one command → client instance on a VPS (~$10/mo per client)
3. Channel adapters as thin POSTers to /handle (Twilio SMS, email poller,
   web-form widget, voice via Whisper+TTS — all equal citizens, none primary)
4. Per-client token/cost tracking
5. pytest suite alongside stress_test.py
6. Customer-identity sessions (map phone/email → session automatically)

## 11. Honest boundaries (tell any AI reading this)

- Swarm's judgment = the plugged-in model's judgment. Architecture is
  frontier-grade; capability follows the brain.
- Real integrations (QuickBooks, Clio, Twilio...) need credentials + testing;
  Toolsmith generates safe stubs until then.
- Prompts improve through iteration on real transcripts (PROMPTING.md §10) —
  the platform ships with good defaults, greatness comes from client feedback
  loops.
- Production clients should run on a VPS, not the dev laptop.

## 12. Context for collaborators

Related working docs (in Sanjay's Cowork outputs folder, not this repo):
ai-for-smb-playbook.md (market research), lewisville-target-list.md and
lewisville-call-list-and-scripts.md (17 real local prospects + cold-call
scripts), client-discovery-checklist.md (the meeting that produces Builder
transcripts), m5-pro-ai-build-spec.md (local model setup for the M5 Pro 48GB),
agent-platform-architecture.md (original architecture spec).

Repo: https://github.com/Sanjayyellina/swarm (public, by owner's choice).
