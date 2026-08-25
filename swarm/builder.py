"""Builder — the meta-agent. Transcript in, working client solution out.

This is the 'give the recording to Claude/GPT and it builds everything' flow,
owned by you: the Builder reads a discovery transcript, designs the smallest
winning solution, writes the agent prompts, and materializes a client folder
the platform can run immediately. The brain is whatever SWARM_MODEL points at
— use the strongest model you have for building; runtime can use smaller ones.

Usage:  python run.py --build examples/sample-transcript.txt --name select-ac
"""
import json
import os

import yaml

from .llm import get_llm


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Builder did not return JSON. Got: {text[:200]}")
    return json.loads(text[start:end + 1])


REQUIRED_KEYS = ("business", "agent", "prompts", "first_build")


def _check_plan(plan):
    missing = [k for k in REQUIRED_KEYS if k not in plan]
    if missing:
        raise ValueError(f"plan missing required keys: {missing}")
    if not plan["agent"].get("tools"):
        raise ValueError("plan agent has no tools")
    if not plan["prompts"]:
        raise ValueError("plan contains no prompt files")
    agent_prompt = plan["agent"].get("prompt")
    if agent_prompt not in plan["prompts"]:
        raise ValueError(f"agent.prompt '{agent_prompt}' has no matching file "
                         f"in prompts ({list(plan['prompts'])})")
    return plan


def _plan_with_retry(llm, system, transcript, attempts=3):
    """Efficiency+accuracy loop: invalid output is fed back as an error and
    regenerated — the Builder fixes its own mistakes instead of needing us."""
    error = None
    for _ in range(attempts):
        user = transcript if not error else (
            f"{transcript}\n\nYour previous BUILD_PLAN was invalid: {error}\n"
            "Output a corrected BUILD_PLAN JSON only.")
        resp = llm.chat([{"role": "system", "content": system},
                         {"role": "user", "content": user}])
        try:
            return _check_plan(_extract_json(resp.content or ""))
        except (ValueError, json.JSONDecodeError) as e:
            error = str(e)
    raise RuntimeError(f"Builder could not produce a valid plan: {error}")


def _critique(llm, system, transcript, plan):
    """Self-correction pass: the Builder re-reads the transcript and checks
    its own plan for invented facts and missed requirements."""
    if getattr(llm, "model", "") == "mock":
        return plan
    prompt = (
        "Review this BUILD_PLAN against the transcript. Check ONLY: "
        "(1) facts not grounded in the transcript (invented hours/prices/"
        "services), (2) owner requirements that were missed (especially "
        "approval/gating requests), (3) tools listed that don't exist. "
        "If the plan is correct respond exactly SAME. Otherwise respond with "
        "the corrected BUILD_PLAN JSON only.\n\n"
        f"TRANSCRIPT:\n{transcript}\n\nBUILD_PLAN:\n{json.dumps(plan)}")
    try:
        resp = llm.chat([{"role": "system", "content": system},
                         {"role": "user", "content": prompt}])
        text = (resp.content or "SAME").strip()
        if text.upper().startswith("SAME"):
            return plan
        return _check_plan(_extract_json(text))
    except Exception:  # noqa: BLE001 — critique must never break a valid build
        return plan


def build_client(transcript_path, name, root_dir):
    with open(transcript_path) as f:
        transcript = f.read()
    with open(os.path.join(root_dir, "prompts", "core", "builder.md")) as f:
        system = f.read()

    llm = get_llm()
    plan = _plan_with_retry(llm, system, transcript)
    plan = _critique(llm, system, transcript, plan)

    client_dir = os.path.join(root_dir, "clients", name)
    os.makedirs(os.path.join(client_dir, "prompts"), exist_ok=True)

    manifest = {
        "name": plan["business"].get("business_name", name),
        "agent": plan["agent"],
        "gates": plan.get("gates", []),
        "specialists": plan.get("specialists", {}),
        "business": {k: v for k, v in plan["business"].items() if v},
    }
    with open(os.path.join(client_dir, "manifest.yaml"), "w") as f:
        yaml.safe_dump(manifest, f, sort_keys=False, allow_unicode=True)

    for fname, content in plan.get("prompts", {}).items():
        with open(os.path.join(client_dir, "prompts", os.path.basename(fname)), "w") as f:
            f.write(content)

    # Autonomy: the swarm builds its own missing tools via the Toolsmith.
    forged = []
    if plan.get("missing_tools"):
        from .toolsmith import build_tool
        for t in plan["missing_tools"]:
            forged.append(build_tool(t["name"], t["description"], root_dir, llm))
        installed = [r["tool"] for r in forged if r["status"] == "installed"]
        # newly installed tools become available to this client's agent
        for tname in installed:
            if tname not in manifest["agent"]["tools"]:
                manifest["agent"]["tools"].append(tname)
        with open(os.path.join(client_dir, "manifest.yaml"), "w") as f:
            yaml.safe_dump(manifest, f, sort_keys=False, allow_unicode=True)

    report = ["# Build Report — " + manifest["name"], ""]
    fb = plan.get("first_build", {})
    report += [f"**First build:** {fb.get('name', 'n/a')}",
               f"**Why:** {fb.get('why', 'n/a')}", ""]
    if forged:
        report += ["## Tools the swarm built for itself", ""]
        for r in forged:
            if r["status"] == "installed":
                report += [f"- **{r['tool']}** — installed and validated "
                           f"(`swarm/tools_generated/{r['tool']}.py` — review before go-live)"]
            else:
                report += [f"- **{r['tool']}** — REJECTED after retry: {r.get('error')}"]
        report += [""]
    if plan.get("open_questions"):
        report += ["## Ask the owner", ""]
        report += [f"- {q}" for q in plan["open_questions"]] + [""]
    if plan.get("future_phases"):
        report += ["## Future phases (do NOT build yet)", ""]
        report += [f"- {p}" for p in plan["future_phases"]] + [""]
    report += ["## Test it", "",
               f"    python run.py --client {name}", ""]
    report_text = "\n".join(report)
    with open(os.path.join(client_dir, "BUILD_REPORT.md"), "w") as f:
        f.write(report_text)
    return client_dir, report_text
