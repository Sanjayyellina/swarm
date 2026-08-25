"""The Swarm loop: plan -> call tools -> check -> respond.

Rules enforced here (from the architecture spec):
  - structured routing: model may only call tools listed in the client manifest
  - approval gates: gated tools pause for human approval
  - max-steps + fallback: never loops forever, never fails silently
"""
import json
import os

import yaml

from . import tools
from .llm import get_llm
from .memory import Memory

MAX_STEPS = 8
MAX_TOOL_RESULT_CHARS = 6000  # token efficiency: huge tool outputs get clipped


def _clip(result):
    if len(result) > MAX_TOOL_RESULT_CHARS:
        return result[:MAX_TOOL_RESULT_CHARS] + '... [truncated for context efficiency]"}'
    return result


class Swarm:
    def __init__(self, client_dir, approve_fn=None):
        self.client_dir = client_dir
        # make previously generated (swarm-built) tools available
        from .toolsmith import load_generated
        load_generated(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(client_dir, "manifest.yaml")) as f:
            self.manifest = yaml.safe_load(f)
        self._validate_manifest(client_dir)
        self.memory = Memory(client_dir)
        self.llm = get_llm()
        # SECURE BY DEFAULT: with no approver attached (API/worker mode),
        # gated actions are DENIED and queued for the owner — never auto-run.
        self.approve_fn = approve_fn or self._deny_and_queue
        prompt_file = os.path.join(client_dir, "prompts",
                                   os.path.basename(self.manifest["agent"]["prompt"]))
        with open(prompt_file) as f:
            self.system_prompt = self._render(f.read())

    def _validate_manifest(self, client_dir):
        """Fail fast with a clear message instead of a KeyError mid-call."""
        m = self.manifest or {}
        # version stamp: tolerated if absent (treated as 1), enables future
        # config migrations to know what they're reading.
        m.setdefault("version", 1)
        problems = []
        if not isinstance(m.get("agent"), dict):
            problems.append("missing 'agent' section")
        else:
            if not m["agent"].get("prompt"):
                problems.append("agent.prompt is required")
            if not isinstance(m["agent"].get("tools"), list) or not m["agent"]["tools"]:
                problems.append("agent.tools must be a non-empty list")
            else:
                prompt_path = os.path.join(client_dir, "prompts",
                                           os.path.basename(m["agent"].get("prompt", "")))
                if not os.path.exists(prompt_path):
                    problems.append(f"prompt file not found: {prompt_path}")
        if problems:
            raise ValueError(f"Invalid manifest for {client_dir}: " + "; ".join(problems))

    def _compact(self, session, threshold=40, keep=16):
        """Context compaction (frontier pattern): when history grows past the
        threshold, distill the older half into durable facts and trim it.
        The conversation keeps its recent detail; the past becomes knowledge."""
        if self.memory.history_count(session) <= threshold:
            return
        old = self.memory.get_history(session, limit=200)[:-keep]
        transcript = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in old)
        try:
            resp = self.llm.chat([{"role": "user", "content":
                "Distill this conversation history into at most 8 durable facts "
                "worth remembering (names, numbers, commitments, preferences, "
                "open issues). One fact per line, no bullets, no commentary:\n\n"
                + transcript}])
            for line in (resp.content or "").splitlines():
                line = line.strip("-• \t")
                if len(line) > 10:
                    self.memory.remember(session, line)
            self.memory.trim_history(session, keep)
        except Exception:  # noqa: BLE001 — compaction is an optimization, never fatal
            pass

    def _deny_and_queue(self, action, args):
        self.memory.take_message(
            "system", "n/a",
            f"APPROVAL NEEDED: swarm wants to run '{action}' with {str(args)[:200]}")
        return False

    def _render(self, template):
        biz = self.manifest.get("business", {})
        for key, val in biz.items():
            template = template.replace("{{" + key + "}}", str(val))
        return template

    def dispatch(self, user_message, session="default"):
        """Smart entry point: dynamically sizes the machinery to the request.
        Trivial → direct (free routing). Objective-shaped → the full company.
        Set agent.routing: direct in the manifest to disable auto-routing."""
        if self.manifest["agent"].get("routing", "auto") != "auto":
            return self.handle(user_message, session)
        from .company import run_company
        from .router import route
        verdict = route(user_message, self)
        self.memory.log_event("router", "route",
                              (user_message or "")[:120], verdict)
        if verdict["tier"] in ("company", "team"):
            entry = verdict.get("role") if verdict["tier"] == "team" else None
            try:
                res = run_company(user_message, self, entry_role=entry)
            except FileNotFoundError:  # no org chart configured → stay direct
                return self.handle(user_message, session)
            if "result" in res:
                self.memory.add_history(session, "user", user_message)
                self.memory.add_history(session, "assistant", res["result"])
                return res["result"]
            # org path failed → the front-line agent still answers
        return self.handle(user_message, session)

    def handle(self, user_message, session="default"):
        """Public entry: wraps the loop with per-turn cost accounting."""
        prev = dict(getattr(self.llm, "usage", {}) or {})
        try:
            return self._handle_inner(user_message, session)
        finally:
            u = getattr(self.llm, "usage", None)
            if u:
                delta = {k: u.get(k, 0) - prev.get(k, 0) for k in u}
                if delta.get("calls"):
                    self.memory.log_event("llm", "usage", session, delta)

    def _handle_inner(self, user_message, session="default"):
        allowed = self.manifest["agent"]["tools"]
        gated = set(self.manifest.get("gates", []))
        user_message = (user_message or "")[:8000]  # input cap: no context bombs
        ctx = {"memory": self.memory, "manifest": self.manifest,
               "client_dir": self.client_dir, "session": session,
               "root_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
               "llm": self.llm}

        self._compact(session)
        system = self.system_prompt
        facts = self.memory.recall(session)
        if facts:
            system += ("\n\n[KNOWN FACTS about this customer/session — use them, "
                       "don't re-ask]\n" + "\n".join(f"- {f}" for f in facts))
        messages = [{"role": "system", "content": system}]
        messages += self.memory.get_history(session)
        messages.append({"role": "user", "content": user_message})
        self.memory.add_history(session, "user", user_message)

        tools_used = False
        for _ in range(MAX_STEPS):
            try:
                resp = self.llm.chat(messages, tools=tools.schemas_for(allowed))
            except Exception as e:  # noqa: BLE001 — LLM outage must not lose the request
                self.memory.take_message(
                    "system", "n/a",
                    f"LLM ERROR ({type(e).__name__}) while handling: {user_message[:200]}")
                reply = ("Sorry — I'm having a technical moment. I've saved your "
                         "request and the team will follow up with you directly.")
                self.memory.add_history(session, "assistant", reply)
                return reply
            if not resp.tool_calls:
                reply = resp.content or "Sorry, I didn't catch that."
                # Efficiency: only run the QA pass when this turn actually did
                # something (used tools) — greetings don't need review.
                if tools_used:
                    reply = self._verify(user_message, reply, messages)
                self.memory.add_history(session, "assistant", reply)
                return reply
            tools_used = True
            # record the assistant's tool-call turn
            messages.append({"role": "assistant", "content": resp.content or "",
                             "tool_calls": [{"id": t["id"], "type": "function",
                                             "function": {"name": t["name"],
                                                          "arguments": json.dumps(t["arguments"])}}
                                            for t in resp.tool_calls]})
            for tc in resp.tool_calls:
                if tc["name"] not in allowed:
                    result = '{"error": "tool not permitted for this client"}'
                elif tc["name"] in gated and not self.approve_fn(tc["name"], tc["arguments"]):
                    result = '{"error": "owner declined this action"}'
                else:
                    result = tools.execute(tc["name"], tc["arguments"], ctx)
                self.memory.log_event("orchestrator", tc["name"],
                                      tc["arguments"], result)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": _clip(result)})
        return self._fallback(user_message, session)

    def _verify(self, user_message, reply, messages):
        """Self-verification pass (frontier pattern): a QA check of the final
        reply against the agent's own hard rules. Enabled via manifest
        agent.verify: true. One corrective rewrite max — never a loop."""
        if not self.manifest["agent"].get("verify"):
            return reply
        if getattr(self.llm, "model", "") == "mock":
            return reply  # deterministic mock brain skips QA
        qa_prompt = (
            "You are a strict QA reviewer. Below are an agent's instructions, "
            "a user message, and the agent's reply. Check the reply ONLY for: "
            "(1) invented facts/prices/times not grounded in the conversation, "
            "(2) violations of the instructions' hard rules, (3) leaving the "
            "user without a next step. If acceptable, respond exactly PASS. "
            "Otherwise respond FAIL: <one-line reason>.\n\n"
            f"INSTRUCTIONS:\n{self.system_prompt}\n\n"
            f"USER: {user_message}\n\nREPLY: {reply}")
        try:
            verdict = self.llm.chat([{"role": "user", "content": qa_prompt}])
            text = (verdict.content or "PASS").strip()
            if text.upper().startswith("PASS"):
                return reply
            fix = self.llm.chat(messages + [
                {"role": "user", "content":
                 f"A reviewer rejected your reply: {text}. "
                 "Rewrite the reply correcting that issue. Output only the reply."}])
            return fix.content or reply
        except Exception:  # noqa: BLE001 — QA must never break the answer path
            return reply

    def _fallback(self, user_message, session):
        # fallback: never fail silently
        fallback = ("I want to make sure this is handled right, so I've flagged it "
                    "for the team — someone will follow up with you directly.")
        self.memory.take_message("system", "n/a",
                                 f"NEEDS HUMAN: could not complete: {user_message}")
        self.memory.add_history(session, "assistant", fallback)
        return fallback
