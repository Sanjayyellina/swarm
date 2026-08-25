"""Toolsmith — the swarm writes its own tools.

Given a missing-tool spec from the Builder, the Toolsmith generates the
Python module, validates it (syntax + clean import + registry check), and
installs it into swarm/tools_generated/. Invalid code is rejected and
retried once with the error fed back — the same write/test/fix loop a
human engineer runs.

Generated code lands in git, so you can always review what your swarm
built for itself. Tools only become callable when a client manifest lists
them, and gates still apply.
"""
import importlib.util
import os
import re

GENERATED_DIR = "tools_generated"


def _strip_fences(text):
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


# Static denylist: generated code containing these is rejected BEFORE any
# import/execution. The Toolsmith has no legitimate need for them; a real
# integration needing subprocess/sockets is written by a human, not generated.
FORBIDDEN = ("subprocess", "os.system", "os.popen", "eval(", "exec(",
             "__import__", "socket", "shutil.rmtree", "os.remove",
             "os.unlink", "pickle", "ctypes")


def _static_scan(code):
    hits = [f for f in FORBIDDEN if f in code]
    if hits:
        raise ValueError(f"forbidden constructs in generated code: {hits}")


def _validate(path, name):
    from . import tools
    before = set(tools.REGISTRY)
    spec = importlib.util.spec_from_file_location(f"gen_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # raises on syntax/import errors
    added = set(tools.REGISTRY) - before
    if name not in tools.REGISTRY:
        raise ValueError(f"module did not register a tool named '{name}' "
                         f"(registered: {sorted(added) or 'none'})")


def build_tool(name, description, root_dir, llm):
    # Name is used as a filename and identifier — sanitize hard.
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name or ""):
        return {"tool": name, "status": "rejected",
                "error": "invalid tool name (need lowercase snake_case, 3-41 chars)"}
    gen_dir = os.path.join(root_dir, "swarm", GENERATED_DIR)
    os.makedirs(gen_dir, exist_ok=True)
    with open(os.path.join(root_dir, "prompts", "core", "toolsmith.md")) as f:
        system = f.read()
    task = f"Tool name: {name}\nWhat it must do: {description}"
    path = os.path.join(gen_dir, f"{name}.py")

    error = None
    for attempt in range(2):
        prompt_task = task if not error else (
            f"{task}\n\nYour previous attempt failed validation with:\n{error}\n"
            "Fix it. Output ONLY the corrected Python code.")
        resp = llm.chat([{"role": "system", "content": system},
                         {"role": "user", "content": prompt_task}])
        code = _strip_fences(resp.content or "")
        try:
            _static_scan(code)
            with open(path, "w") as f:
                f.write(code)
            _validate(path, name)
            return {"tool": name, "status": "installed", "path": path}
        except Exception as e:  # noqa: BLE001 — feed error back for the retry
            error = str(e)
    if os.path.exists(path):
        os.rename(path, path + ".rejected")
    return {"tool": name, "status": "rejected", "error": error,
            "note": "left as .rejected for human review"}


def load_generated(root_dir):
    """Import every previously generated tool so the registry knows them."""
    gen_dir = os.path.join(root_dir, "swarm", GENERATED_DIR)
    if not os.path.isdir(gen_dir):
        return []
    loaded = []
    for fname in sorted(os.listdir(gen_dir)):
        if fname.endswith(".py"):
            path = os.path.join(gen_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(
                    f"gen_{fname[:-3]}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                loaded.append(fname[:-3])
            except Exception:  # noqa: BLE001 — a bad tool must not kill the swarm
                pass
    return loaded
