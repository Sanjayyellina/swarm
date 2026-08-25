# Swarm — your AI agent platform (v0.4)

**Universal by design.** Swarm is not a receptionist, a chatbot, or a voice
product. It's a general agent platform: any input source (HTTP API), any
timing (reactive via messages, proactive via the scheduler), any integration
(allowlisted `http_request` + generated tools), any business. What each client
gets is decided by discovery and materialized by the Builder from a transcript.

```
python run.py --build meeting.txt --name client-x   # Builder: transcript → solution
python run.py --client client-x                     # chat with it
python run.py --client client-x --serve 8080        # universal HTTP API
python run.py --client client-x --work              # scheduler (proactive work)
```

One core, configured per client. The same architecture that powers frontier AI
assistants: an agentic loop (plan → call tools → check → respond) + a tool
registry + per-client memory + human approval gates. The model behind it is a
config line — API today, your M5 Pro's local models tomorrow, your NVIDIA box
after that.

## Quick start

```bash
cd swarm
pip install -r requirements.txt

# 1. Prove the machinery works (no API key, no network):
python run.py --client demo-hvac --test

# 2. Go live with your API key:
cp .env.example .env        # pick a provider block, fill the key
export $(grep -v '^#' .env | xargs)
python run.py --client demo-hvac
```

Then chat as a caller: *"Hi, my AC died, can someone come out tomorrow?"*

## Layout

```
swarm/
  swarm/
    orchestrator.py   the loop: routing, gates, max-steps, fallback-to-human
    llm.py            one client for ANY brain (OpenAI/Groq/Anthropic/LM Studio/Ollama) + mock
    tools.py          tool registry — your compounding asset; add tools here
    memory.py         per-client SQLite: appointments, messages, conversation history
  clients/
    demo-hvac/
      manifest.yaml   which agent/tools/gates/business facts this client gets
      prompts/        the client's voice and rules
      db/             their isolated data (auto-created)
  run.py              CLI chat + self-test
```

## How to onboard a real client (the 2-day pattern)

1. `cp -r clients/demo-hvac clients/<their-name>` and delete the db/ folder.
2. Edit `manifest.yaml`: business facts, allowed tools, gated tools.
3. Rewrite `prompts/receptionist.md` (or add new prompts) from your discovery notes.
4. Add any missing tools to `swarm/tools.py` (their calendar, their CRM...).
5. Test locally, then deploy the folder + code to their own small VPS.

## Switching brains

Edit `.env` (see `.env.example`). Options already wired:
OpenAI · Groq · Anthropic (OpenAI-compat endpoint) · **LM Studio (localhost:1234)** ·
**Ollama (localhost:11434)**. When the M5 Pro arrives, uncomment the LM Studio
block and Swarm runs 100% local and free.

Env extras: `SWARM_MOCK=1` (deterministic test brain), `SWARM_DB_DIR=<path>`
(relocate databases, e.g. to fast local disk).

## v0.2 — the frontier patterns

- **Subagents** (`swarm/subagents.py`): the orchestrator delegates via the
  `delegate` tool to specialists driven by `prompts/core/*` (client overrides
  in `clients/<x>/prompts/`). Specialists get no tools unless the manifest
  grants them, and can run on their own model (`specialists.<name>.model`) —
  big brain plans, small brains execute.
- **Self-verification** (`agent.verify: true`): a QA pass checks the final
  reply for invented facts, rule violations, and dead-ends; one corrective
  rewrite max, and QA failure can never break the answer path.
- **Model routing**: every `llm.chat()` accepts a per-call model override.

See `PROMPTING.md` for the prompt-engineering principles behind the pack.

## Roadmap (add as client work demands — not before)

- [x] Subagents: orchestrator delegating to specialist prompts
- [x] Self-verification pass: second model call that checks the first's work
- [ ] Channels: Twilio SMS webhook → `Swarm.handle()`; then voice (Whisper + TTS)
- [ ] Email channel (IMAP poll → handle → SMTP reply)
- [ ] Owner dashboard: view messages/bookings, approve gated actions from a phone
- [ ] Long-term memory notes: distilled facts per customer surviving history limits
- [ ] Deploy script: one command → client instance on a VPS

## Design rules (do not break these)

- The model only ever calls tools listed in the client's manifest.
- Money-spending / mass-sending / legally-binding actions go in `gates`.
- Every failure path ends in a human being notified — never silence.
- One client = one folder = one database. No shared state between clients.
