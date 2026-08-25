"""LLM client — one abstraction, any brain.

Reads config from environment:
  SWARM_BASE_URL  e.g. https://api.openai.com/v1  |  http://localhost:1234/v1 (LM Studio)
                       http://localhost:11434/v1 (Ollama) | https://api.groq.com/openai/v1
  SWARM_API_KEY   provider key ("lm-studio" / "ollama" for local — any non-empty string works)
  SWARM_MODEL     e.g. gpt-4o-mini | qwen3-32b | llama-3.3-70b-versatile
  SWARM_MOCK=1    use the deterministic mock brain (no network, for tests)

The day the M5 Pro arrives: point SWARM_BASE_URL at LM Studio, done.
"""
import json
import os
import re


class LLMResponse:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []  # list of {"id", "name", "arguments"(dict)}


def _load_registry():
    """models.yaml: alias → provider/model/price. Optional; absent = env-only."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "models.yaml")
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _env_val(v):
    """'env:VAR' → value of $VAR; anything else passes through."""
    if isinstance(v, str) and v.startswith("env:"):
        return os.environ.get(v[4:], "")
    return v


class LLM:
    def __init__(self):
        from openai import OpenAI
        self._OpenAI = OpenAI
        self.registry = _load_registry()
        self.model = os.environ.get("SWARM_MODEL", "gpt-4o-mini")
        self.client = OpenAI(
            base_url=os.environ.get("SWARM_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ.get("SWARM_API_KEY", ""),
        )
        self._alias_clients = {}
        # Cost tracking: tokens AND dollars (prices from models.yaml).
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cost_usd": 0.0}

    def _resolve(self, model):
        """alias → (client, real model name, price_in, price_out).
        Unknown names pass through to the default client (back-compat)."""
        d = self.registry.get("default", {}) or {}
        if not model:
            return self.client, self.model, d.get("price_in", 0), d.get("price_out", 0)
        cfg = (self.registry.get("aliases") or {}).get(model)
        if not cfg:
            return self.client, model, d.get("price_in", 0), d.get("price_out", 0)
        key = (cfg.get("base_url", ""), cfg.get("api_key", ""))
        if key not in self._alias_clients:
            self._alias_clients[key] = self._OpenAI(
                base_url=cfg.get("base_url") or "https://api.openai.com/v1",
                api_key=_env_val(cfg.get("api_key", "")))
        return (self._alias_clients[key], cfg.get("model", self.model),
                cfg.get("price_in", 0), cfg.get("price_out", 0))

    def chat(self, messages, tools=None, model=None):
        import time as _t
        client, model_name, p_in, p_out = self._resolve(model)
        kwargs = {"model": model_name, "messages": messages, "timeout": 60}
        if tools:
            kwargs["tools"] = tools
        # Reliability: transient API failures (rate limits, blips) get two
        # retries with backoff before the orchestrator's fallback kicks in.
        last_err = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(**kwargs)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2:
                    _t.sleep(1.5 ** attempt)
        else:
            raise last_err
        self.usage["calls"] += 1
        u = getattr(resp, "usage", None)
        if u:
            pt = getattr(u, "prompt_tokens", 0) or 0
            ct = getattr(u, "completion_tokens", 0) or 0
            self.usage["prompt_tokens"] += pt
            self.usage["completion_tokens"] += ct
            self.usage["cost_usd"] += (pt * (p_in or 0) + ct * (p_out or 0)) / 1e6
        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        return LLMResponse(content=msg.content, tool_calls=tool_calls)


class MockLLM:
    """Deterministic brain for tests/demos without a network.

    Simulates a receptionist: greets, checks availability when asked to book,
    books when it has name+phone+time, takes a message otherwise.
    """

    def __init__(self):
        self.model = "mock"

    def chat(self, messages, tools=None, model=None):
        tool_names = {t["function"]["name"] for t in (tools or [])}
        last_user = next((m["content"] for m in reversed(messages)
                          if m["role"] == "user"), "")
        # Engineer mode has its own scripted loop — handle before anything else.
        _sys = next((m["content"] for m in messages if m["role"] == "system"), "")
        if _sys.startswith("# ENGINEER"):
            return self._engineer_step(messages)
        # Company mode: scripted chain of command for offline testing.
        if _sys.startswith("# ROLE:"):
            return self._role_step(_sys, messages, tools)
        # Router mode: keyword triage mirroring the real router prompt.
        if _sys.startswith("# ROUTER"):
            low = last_user.lower()
            if any(w in low for w in ("program", "overhaul", "audit",
                                      "templates", "document the")):
                v = {"tier": "company", "reason": "mock: multi-part objective"}
            elif any(w in low for w in ("remind", "chase", "invoice",
                                        "schedule a", "note that")):
                v = {"tier": "team", "role": "senior_ops",
                     "reason": "mock: contained ops job"}
            else:
                v = {"tier": "direct", "reason": "mock triage"}
            return LLMResponse(content=json.dumps(v))
        # If we just got a tool result back, respond based on it.
        last = messages[-1]
        if last["role"] == "tool":
            result = last["content"]
            if "available_slots" in result and "book_appointment" in tool_names:
                info = self._extract(messages)
                slot = json.loads(result)["available_slots"][0]
                return LLMResponse(tool_calls=[{
                    "id": "mock2", "name": "book_appointment",
                    "arguments": {"name": info["name"], "phone": info["phone"],
                                  "start": slot, "service": info["service"]}}])
            if "booked" in result:
                data = json.loads(result)
                return LLMResponse(content=(
                    f"You're all set! I've booked your {data['service']} for "
                    f"{data['start']}. We'll text a confirmation to {data['phone']}."))
            if "message_id" in result:
                return LLMResponse(content="I've passed your message to the team — "
                                           "someone will call you back shortly.")
            if '"scheduled": true' in result:
                return LLMResponse(content=(
                    "Done — I've scheduled the follow-up. I'll chase it "
                    "automatically and let you know when it's resolved."))
            return LLMResponse(content="Done. Anything else I can help with?")
        # Compaction distill prompts: return nothing (no junk facts in tests).
        if last_user.startswith("Distill this conversation"):
            return LLMResponse(content="")
        # Toolsmith mode: return canned tool code so autonomy is testable offline.
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if system.startswith("# TOOLSMITH"):
            user = messages[-1]["content"]
            tname = re.search(r"Tool name:\s*(\w+)", user)
            tname = tname.group(1) if tname else "generated_tool"
            return LLMResponse(content=(
                "from swarm.tools import tool\n\n"
                f"@tool(\"{tname}\", \"Auto-generated stub: records intent for "
                "the owner until real integration is configured.\",\n"
                "      {\"properties\": {\"details\": {\"type\": \"string\"}},\n"
                "       \"required\": [\"details\"]})\n"
                f"def {tname}(ctx, details):\n"
                "    ctx[\"memory\"].take_message(\"system\", \"n/a\",\n"
                f"        f\"QUEUED ({tname}): {{details}}\")\n"
                "    return {\"status\": \"queued_for_owner\", \"details\": details}\n"))
        if system.startswith("# BUILDER"):
            return LLMResponse(content=json.dumps({
                "business": {"business_name": "Mock Plumbing Co", "owner_name": "Alex",
                             "services": "drain cleaning, water heaters",
                             "hours": "Mon-Fri 8-5", "service_area": "Lewisville TX"},
                "first_build": {"name": "After-hours call capture",
                                "why": "Owner said evening calls go to voicemail."},
                "agent": {"prompt": "receptionist.md", "verify": True,
                          "tools": ["current_time", "check_availability",
                                    "book_appointment", "take_message"]},
                "gates": ["cancel_appointment"],
                "specialists": {"summarizer": {}},
                "prompts": {"receptionist.md":
                            "# RECEPTIONIST - Mock Plumbing Co\n[IDENTITY]...\n"},
                "missing_tools": [{"name": "quickbooks_invoice",
                                   "description": "create invoice from job notes"}],
                "future_phases": ["estimate follow-up chaser"],
                "open_questions": ["Confirm emergency dispatch process"]}))
        # Fresh user message.
        text = last_user.lower()
        # Back-office flow: missing documents → schedule a chase sequence.
        if any(w in text for w in ("bank statement", "missing document", "paperwork",
                                   "hasn't sent", "chase")) and "schedule_task" in tool_names:
            return LLMResponse(tool_calls=[{
                "id": "mockc", "name": "schedule_task",
                "arguments": {"minutes_from_now": 4320,
                              "task": f"Follow up about missing documents: {last_user[:120]}"}}])
        if any(w in text for w in ("book", "appointment", "schedule", "come out")):
            info = self._extract(messages)
            if info["phone"] == "unknown" or info["name"] == "Unknown":
                return LLMResponse(content=(
                    "Sorry to hear that — we can definitely get someone out. "
                    "Can I grab your name and the best number to reach you?"))
            if "check_availability" in tool_names:
                return LLMResponse(tool_calls=[{
                    "id": "mock1", "name": "check_availability",
                    "arguments": {"day": "tomorrow"}}])
        if any(w in text for w in ("message", "call me back", "tell them")):
            info = self._extract(messages)
            return LLMResponse(tool_calls=[{
                "id": "mock3", "name": "take_message",
                "arguments": {"name": info["name"], "phone": info["phone"],
                              "body": last_user}}])
        return LLMResponse(content="Thanks for calling! How can I help — would you "
                                   "like to book an appointment or leave a message?")

    @staticmethod
    def _role_step(system, messages, tools):
        """Scripted org-chart behavior: leaders assign down, ICs do the work,
        reviewers approve on the way back up."""
        tool_names = {t["function"]["name"] for t in (tools or [])}
        role = system.split("\n")[0].replace("# ROLE:", "").strip()
        task = next((m["content"] for m in messages if m["role"] == "user"), "")
        tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        if "assign" in tool_names:
            if not tool_results:
                # leader with no results yet: delegate down the chart
                first = None
                for t in (tools or []):
                    fn = t["function"]
                    if fn["name"] == "assign":
                        first = fn["parameters"]["properties"]["role"]["enum"][0]
                if first:
                    return LLMResponse(tool_calls=[{
                        "id": "a1", "name": "assign",
                        "arguments": {"role": first,
                                      "task": f"[from {role}] {task[:150]}"}}])
            # results are back: review and pass up
            return LLMResponse(content=f"{role.upper()} reviewed and approved: "
                                       f"{tool_results[-1][:120]}")
        # individual contributor: do the work
        return LLMResponse(content=f"{role.upper()} completed: {task[:100]}")

    @staticmethod
    def _engineer_step(messages):
        """Scripted self-development round-trip: list → stage → validate → report."""
        tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        if not tool_results:
            return LLMResponse(tool_calls=[{"id": "e1", "name": "list_files",
                                            "arguments": {}}])
        last_result = tool_results[-1]
        if '"files"' in last_result:
            return LLMResponse(tool_calls=[{
                "id": "e2", "name": "write_staged",
                "arguments": {"path": "IMPROVEMENT.md",
                              "content": "# Mock improvement\nProof of the "
                                         "self-development loop.\n"}}])
        if '"staged"' in last_result:
            return LLMResponse(tool_calls=[{"id": "e3", "name": "run_validation",
                                            "arguments": {}}])
        if '"passed": true' in last_result:
            return LLMResponse(content="Report: staged IMPROVEMENT.md; "
                                       "validation passed; no risks.")
        return LLMResponse(content="Report: validation failed — see output.")

    @staticmethod
    def _extract(messages):
        blob = " ".join(m["content"] or "" for m in messages if m["role"] == "user")
        phone = re.search(r"[\d\-\(\)\. ]{7,}", blob)
        name = re.search(r"(?:name is|this is|i'?m)\s+([A-Za-z]+)", blob, re.I)
        service = "service call"
        for s in ("ac repair", "heater", "tune-up", "leak", "water heater"):
            if s in blob.lower():
                service = s
        return {"name": name.group(1).title() if name else "Unknown",
                "phone": phone.group(0).strip() if phone else "unknown",
                "service": service}


def get_llm():
    if os.environ.get("SWARM_MOCK") == "1":
        return MockLLM()
    return LLM()
