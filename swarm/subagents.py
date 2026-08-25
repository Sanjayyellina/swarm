"""Subagents — the orchestrator's specialists.

This is the same pattern frontier assistants use: the main agent doesn't do
specialist work itself; it spawns a focused sub-conversation with a specialist
prompt (extractor, classifier, drafter, chaser, summarizer...) and gets back
just the result. Each specialist can run on its own model — a small fast one
for narrow jobs, the big one for hard reasoning.

Prompt resolution order:
  1. clients/<client>/prompts/<name>.md   (client-customized specialist)
  2. prompts/core/<name>.md               (the shared frontier-grade pack)
"""
import os

from . import tools

MAX_SPECIALIST_STEPS = 4

# Sensible defaults for template slots a client manifest may not fill.
DEFAULTS = {
    "schema": ('{"name": string|null, "phone": string|null, '
               '"request": string|null, "urgency": "emergency"|"routine"|null, '
               '"preferred_time": string|null, "notes": string|null}'),
    "labels": "emergency | booking_request | price_question | complaint | "
              "callback_request | vendor_or_spam | other",
    "voice_notes": "Friendly, plain-spoken, short sentences, first names.",
    "wait_days": "3",
    "send_window": "9am-6pm local time, weekdays",
    "max_steps": "8",
}


def _render(template, manifest, extra=None):
    values = dict(DEFAULTS)
    values.update(manifest.get("business", {}))
    values.update(extra or {})
    for key, val in values.items():
        template = template.replace("{{" + key + "}}", str(val))
    return template


def _find_prompt(name, ctx):
    candidates = [
        os.path.join(ctx["client_dir"], "prompts", f"{name}.md"),
        os.path.join(ctx["root_dir"], "prompts", "core", f"{name}.md"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
    return None


import re as _re


def run_specialist(name, task, ctx):
    """Run one specialist on one task. Returns a dict with the result."""
    # Name becomes a file path component — sanitize against traversal.
    if not _re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", name or ""):
        return {"error": f"invalid specialist name '{name}'"}
    spec_cfg = (ctx["manifest"].get("specialists") or {}).get(name, {})
    template = _find_prompt(name, ctx)
    if template is None:
        return {"error": f"no prompt found for specialist '{name}'"}

    system_prompt = _render(template, ctx["manifest"],
                            extra=spec_cfg.get("vars", {}))
    allowed_tools = [t for t in spec_cfg.get("tools", []) if t != "delegate"]
    # specialists get NO tools unless granted, and NEVER 'delegate' —
    # subagents cannot spawn subagents (no recursion, bounded depth)
    model = spec_cfg.get("model")              # None = inherit default brain

    llm = ctx["llm"]
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": task}]

    for _ in range(MAX_SPECIALIST_STEPS):
        kwargs = {"tools": tools.schemas_for(allowed_tools)} if allowed_tools else {}
        try:
            resp = llm.chat(messages, model=model, **kwargs)
        except TypeError:  # brains without model-override support (e.g. mock)
            resp = llm.chat(messages, **kwargs)
        if not resp.tool_calls:
            return {"agent": name, "result": resp.content or ""}
        import json as _json
        messages.append({"role": "assistant", "content": resp.content or "",
                         "tool_calls": [{"id": t["id"], "type": "function",
                                         "function": {"name": t["name"],
                                                      "arguments": _json.dumps(t["arguments"])}}
                                        for t in resp.tool_calls]})
        gated = set(ctx["manifest"].get("gates", []))
        for tc in resp.tool_calls:
            if tc["name"] not in allowed_tools:
                result = '{"error": "tool not permitted for this specialist"}'
            elif tc["name"] in gated:
                # Specialists NEVER execute gated actions — they queue them
                # for the orchestrator/owner. Defense in depth.
                result = ('{"error": "gated action - specialists cannot execute '
                          'this; report it back so the owner can approve"}')
            else:
                result = tools.execute(tc["name"], tc["arguments"], ctx)
            ctx["memory"].log_event(f"specialist:{name}", tc["name"],
                                    tc["arguments"], result)
            if len(result) > 6000:  # context efficiency
                result = result[:6000] + '... [truncated]"}'
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": result})
    return {"agent": name, "result": None,
            "error": "specialist exceeded step budget"}
