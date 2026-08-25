"""Engineer — Swarm builds Swarm.

The self-development loop, with the discipline of a senior engineer:
  1. reads its own source (list_files / read_source)
  2. writes changes ONLY to .staging/ — live code is never touched
  3. run_validation copies the repo to a sandbox, overlays the staged files,
     and runs the full self-test + 31-test stress suite there
  4. only a passing change is offered; `--apply` copies staging over live

Use the strongest brain you have for this (SWARM_MODEL); engineering is the
one job where model quality matters most.

  python run.py --improve "add a /metrics endpoint to the API server"
  python run.py --apply          # after reviewing .staging/
"""
import json
import os
import shutil
import subprocess
import sys

from .llm import get_llm

MAX_ENGINEER_STEPS = 20
IGNORED_DIRS = {".git", "__pycache__", ".staging", "db", "workspace",
                "tools_generated"}
FORBIDDEN_FILES = {".env"}


def _tree(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for f in filenames:
            if f in FORBIDDEN_FILES or not f.endswith((".py", ".md", ".yaml", ".txt")):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(out)


def _safe_rel(root, rel):
    """Resolve rel inside root; refuse escapes and forbidden files."""
    rel = rel.lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(os.path.realpath(root) + os.sep):
        raise ValueError(f"path escapes repo: {rel}")
    if os.path.basename(full) in FORBIDDEN_FILES:
        raise ValueError("access to credentials is forbidden")
    return full, rel


SCHEMAS = [
    {"type": "function", "function": {"name": "list_files",
     "description": "List all source files in the Swarm repo.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "read_source",
     "description": "Read one source file from the repo.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "relative path"}},
         "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_staged",
     "description": "Write the COMPLETE new content of a file to staging "
                    "(never touches live code).",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_validation",
     "description": "Copy repo + staged changes to a sandbox and run the full "
                    "test suites there. Returns pass/fail and output.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def _run_validation(root):
    sandbox = os.path.join("/tmp", "swarm_validate")
    shutil.rmtree(sandbox, ignore_errors=True)
    shutil.copytree(root, sandbox, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".staging", "db", "workspace", "*.pyc"))
    staging = os.path.join(root, ".staging")
    if os.path.isdir(staging):
        for dirpath, _, filenames in os.walk(staging):
            for f in filenames:
                src = os.path.join(dirpath, f)
                rel = os.path.relpath(src, staging)
                dst = os.path.join(sandbox, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
    env = dict(os.environ, SWARM_MOCK="1", SWARM_DB_DIR="/tmp/swarm_validate_db")
    shutil.rmtree("/tmp/swarm_validate_db", ignore_errors=True)
    results = []
    for cmd in ([sys.executable, "run.py", "--client", "demo-hvac", "--test"],
                [sys.executable, "stress_test.py"]):
        try:
            p = subprocess.run(cmd, cwd=sandbox, env=env, timeout=180,
                               capture_output=True, text=True)
            results.append((p.returncode == 0, (p.stdout + p.stderr)[-1500:]))
        except subprocess.TimeoutExpired:
            results.append((False, f"TIMEOUT: {' '.join(cmd)}"))
    passed = all(ok for ok, _ in results)
    return {"passed": passed,
            "output": "\n---\n".join(out for _, out in results)[-2500:]}


def improve(request, root):
    with open(os.path.join(root, "prompts", "core", "engineer.md")) as f:
        system = f.read()
    llm = get_llm()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content":
                 f"IMPROVEMENT REQUEST: {request}\n\nRepo files:\n"
                 + "\n".join(_tree(root))}]
    staged, validated = [], False

    for _ in range(MAX_ENGINEER_STEPS):
        resp = llm.chat(messages, tools=SCHEMAS)
        if not resp.tool_calls:
            report = resp.content or "(no report)"
            return {"staged_files": staged, "validated": validated,
                    "report": report}
        messages.append({"role": "assistant", "content": resp.content or "",
                         "tool_calls": [{"id": t["id"], "type": "function",
                                         "function": {"name": t["name"],
                                                      "arguments": json.dumps(t["arguments"])}}
                                        for t in resp.tool_calls]})
        for tc in resp.tool_calls:
            name, args = tc["name"], tc["arguments"]
            try:
                if name == "list_files":
                    result = {"files": _tree(root)}
                elif name == "read_source":
                    full, rel = _safe_rel(root, args["path"])
                    with open(full, errors="replace") as f:
                        result = {"path": rel, "content": f.read(60000)}
                elif name == "write_staged":
                    _, rel = _safe_rel(root, args["path"])
                    dst = os.path.join(root, ".staging", rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "w") as f:
                        f.write(args["content"])
                    if rel not in staged:
                        staged.append(rel)
                    result = {"staged": rel}
                elif name == "run_validation":
                    result = _run_validation(root)
                    validated = result["passed"]
                else:
                    result = {"error": f"unknown tool {name}"}
            except Exception as e:  # noqa: BLE001 — feed errors back to the model
                result = {"error": str(e)}
            out = json.dumps(result)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": out[:8000]})
    return {"staged_files": staged, "validated": validated,
            "report": "step budget exhausted — review staging manually"}


def apply_staged(root):
    staging = os.path.join(root, ".staging")
    if not os.path.isdir(staging):
        return []
    applied = []
    for dirpath, _, filenames in os.walk(staging):
        for f in filenames:
            src = os.path.join(dirpath, f)
            rel = os.path.relpath(src, staging)
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            applied.append(rel)
    shutil.rmtree(staging)
    return applied
