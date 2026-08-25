"""The Company — a real organizational hierarchy of agents.

Chain-of-command rules (enforced by code, not just prompts):
  - a role may `assign` ONLY to its direct reports in the org chart
  - depth is bounded by the chart itself + MAX_DEPTH backstop (no runaway
    recursion — this replaces the old flat no-recursion rule)
  - results flow back up; each level is prompted to REVIEW before passing up
  - gated tools stay gated; roles below the top cannot trigger approvals
  - every assign and tool call lands in the audit log with the role as actor

Org chart: org.yaml at repo root; clients/<name>/org.yaml overrides it.
Per-role model bands: leadership on the strong model, juniors on cheap ones.

  python run.py --client demo-hvac --company "objective..."
"""
import json
import os

import yaml

from . import tools

MAX_DEPTH = 5
MAX_ROLE_STEPS = 6


def load_org(root_dir, client_dir):
    for path in (os.path.join(client_dir, "org.yaml"),
                 os.path.join(root_dir, "org.yaml")):
        if os.path.exists(path):
            with open(path) as f:
                org = yaml.safe_load(f)
            _validate_org(org)
            return org
    raise FileNotFoundError("no org.yaml found")


def _validate_org(org):
    roles = org.get("roles", {})
    if org.get("top") not in roles:
        raise ValueError("org.top must name a defined role")
    for name, cfg in roles.items():
        for rep in cfg.get("reports", []):
            if rep not in roles:
                raise ValueError(f"role '{name}' reports to undefined '{rep}'")
    # cycle check: walk down from top, must terminate
    def walk(role, seen):
        if role in seen:
            raise ValueError(f"org chart cycle at '{role}'")
        for rep in roles[role].get("reports", []):
            walk(rep, seen | {role})
    walk(org["top"], set())


def _assign_schema(reports):
    return {"type": "function", "function": {
        "name": "assign",
        "description": ("Assign a sub-task to one of YOUR direct reports: "
                        f"{', '.join(reports)}. Include ALL context they need "
                        "— they cannot see your conversation."),
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "enum": reports},
            "task": {"type": "string"}},
            "required": ["role", "task"]}}}


def _role_prompt(role, cfg, ctx):
    from .subagents import _render
    candidates = [os.path.join(ctx["client_dir"], "prompts", "roles",
                               cfg.get("prompt", f"{role}.md")),
                  os.path.join(ctx["root_dir"], "prompts", "roles",
                               cfg.get("prompt", f"{role}.md"))]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return _render(f.read(), ctx["manifest"], extra=cfg.get("vars", {}))
    raise FileNotFoundError(f"no role prompt for '{role}'")


def run_role(role, task, ctx, depth=0):
    if depth >= MAX_DEPTH:
        return {"role": role, "error": "org depth limit reached"}
    org = ctx["org"]
    cfg = org["roles"][role]
    reports = cfg.get("reports", [])
    # Gated tools are NEVER executable in company mode, at any depth — owner
    # approval only flows through the orchestrator's approve_fn. Roles route
    # approval-needing work via take_message instead. (Fixed: depth-0 bypass.)
    gated = set(ctx["manifest"].get("gates", []))
    allowed = [t for t in cfg.get("tools", [])
               if t != "delegate" and t not in gated]
    schemas = tools.schemas_for(allowed)
    if reports:
        schemas = schemas + [_assign_schema(reports)]

    messages = [{"role": "system", "content": _role_prompt(role, cfg, ctx)},
                {"role": "user", "content": task}]
    llm = ctx["llm"]

    for _ in range(MAX_ROLE_STEPS):
        try:
            resp = llm.chat(messages, tools=schemas, model=cfg.get("model"))
        except Exception as e:  # noqa: BLE001
            return {"role": role, "error": f"LLM failure: {e}"}
        if not resp.tool_calls:
            return {"role": role, "result": resp.content or ""}
        messages.append({"role": "assistant", "content": resp.content or "",
                         "tool_calls": [{"id": t["id"], "type": "function",
                                         "function": {"name": t["name"],
                                                      "arguments": json.dumps(t["arguments"])}}
                                        for t in resp.tool_calls]})
        for tc in resp.tool_calls:
            name, args = tc["name"], tc["arguments"]
            if name == "assign":
                target = args.get("role", "")
                if target not in reports:
                    result = {"error": f"'{target}' is not your direct report"}
                else:
                    result = run_role(target, args.get("task", ""), ctx, depth + 1)
                ctx["memory"].log_event(f"role:{role}", "assign",
                                        {"to": target}, str(result)[:300])
            elif name not in allowed:
                result = {"error": "tool not permitted for this role"}
            else:
                result = json.loads(tools.execute(name, args, ctx))
                ctx["memory"].log_event(f"role:{role}", name, args,
                                        str(result)[:300])
            out = json.dumps(result)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": out[:6000]})
    return {"role": role, "error": "step budget exhausted",
            "note": "partial work may exist in tools/audit log"}


def run_company(objective, swarm, entry_role=None):
    """Hand the objective to the org — the whole company (top of chart) or,
    for smaller jobs, directly to one role/sub-team (entry_role). Entering
    mid-chart means only that role and its reports activate: a 'few agents'
    instead of the full hierarchy."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    org = load_org(root_dir, swarm.client_dir)
    entry = entry_role or org["top"]
    if entry not in org["roles"]:
        return {"role": entry, "error": f"unknown role '{entry}'"}
    ctx = {"memory": swarm.memory, "manifest": swarm.manifest,
           "client_dir": swarm.client_dir, "root_dir": root_dir,
           "llm": swarm.llm, "org": org, "session": "company"}
    result = run_role(entry, objective, ctx, depth=0)
    if "error" in result:
        swarm.memory.take_message("system", "n/a",
                                  f"COMPANY RUN FAILED: {objective[:150]} — "
                                  f"{result['error']}")
    return result
