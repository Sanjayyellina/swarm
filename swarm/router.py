"""Router — dynamic depth selection, org-aware.

Cost ladder, cheapest rung that works wins:
  1. FREE heuristic — short conversational messages → direct, zero LLM calls.
  2. One small-model triage call (agent.router_model = your cheapest brain)
     choosing: direct | team (enter the org chart at ONE role — a few agents,
     not the whole company) | company (full hierarchy from the top).

Design decisions, each with its why:
  - default to "direct" on doubt/failure: wrong-but-cheap beats
    wrong-and-expensive, and every agent can escalate later.
  - the router reads the live org chart, so per-client org.yaml changes
    change routing with zero code edits.
  - hallucinated role names are caught in dispatch and demoted to direct.
  - message content is data: embedded routing instructions are ignored.
"""
import json
import os
import re

# Signals that a message might be a real objective (worth a triage call).
# WHY regex: zero-cost, no false negatives that matter — anything complex
# but hint-free is still just one direct conversation away from escalation.
COMPLEX_HINTS = re.compile(
    r"\b(program|project|plan|system|overhaul|audit|all (of|our)|every|"
    r"set ?up|build|create|design|organize|workflow|templates?|and then|"
    r"multiple|report on|remind|chase|invoice|follow[- ]?up)\b", re.I)


def _role_menu(org):
    lines = []
    for name, cfg in org.get("roles", {}).items():
        if name == org.get("top"):
            continue  # top of chart = "company" tier, not a team entry
        desc = cfg.get("desc")
        if desc:
            lines.append(f"- {name}: {desc}")
    return "\n".join(lines) or "- (no team entries defined)"


def route(message, swarm):
    m = (message or "").strip()
    # Rung 1: free heuristic.
    if len(m) < 80 and not COMPLEX_HINTS.search(m):
        return {"tier": "direct", "reason": "trivial (heuristic, zero-cost)"}
    # Rung 2: one cheap triage call, org-aware.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        from .company import load_org
        org = load_org(root, swarm.client_dir)
        with open(os.path.join(root, "prompts", "core", "router.md")) as f:
            system = f.read().replace("{{role_menu}}", _role_menu(org))
        resp = swarm.llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": m[:2000]}],
            model=swarm.manifest["agent"].get("router_model"))
        text = resp.content or ""
        start, end = text.find("{"), text.rfind("}")
        verdict = json.loads(text[start:end + 1])
        tier = verdict.get("tier")
        if tier in ("direct", "company"):
            return verdict
        if tier == "team":
            role = verdict.get("role", "")
            if role in org.get("roles", {}) and role != org.get("top"):
                return verdict
            # hallucinated/invalid role → safe demotion
            return {"tier": "direct", "reason": f"invalid team role '{role}'"}
    except Exception:  # noqa: BLE001 — routing failure must never block work
        pass
    return {"tier": "direct", "reason": "router fallback"}
