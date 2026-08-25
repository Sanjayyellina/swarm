"""Tool registry. Every tool = a function + a JSON schema the model can call.

Adding a tool for a new client = write one function here (or in a new module)
and list its name in that client's manifest.yaml. This registry is the
compounding asset — tools you build for client #1 ship instantly to client #8.
"""
import json
import os
from datetime import datetime, timedelta

REGISTRY = {}


def tool(name, description, parameters):
    """Decorator: register a function as a model-callable tool."""
    def wrap(fn):
        REGISTRY[name] = {
            "fn": fn,
            "schema": {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": parameters.get("properties", {}),
                               "required": parameters.get("required", [])}}},
        }
        return fn
    return wrap


def schemas_for(names):
    return [REGISTRY[n]["schema"] for n in names if n in REGISTRY]


def execute(name, arguments, ctx):
    """Run a tool. ctx carries memory, manifest, etc. Always returns a JSON string."""
    if name not in REGISTRY:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        result = REGISTRY[name]["fn"](ctx=ctx, **arguments)
        return json.dumps(result)
    except Exception as e:  # noqa: BLE001 — tool failures go back to the model
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------- core tools

def _now(ctx):
    """Business-local time. Set business.timezone in the manifest (e.g.
    'America/Chicago') — critical when deployed on a UTC server."""
    tz_name = ctx["manifest"].get("business", {}).get("timezone")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001 — bad tz falls back to server time
            pass
    return datetime.now()


@tool("current_time", "Get the current date and time (business-local).",
      {"properties": {}})
def current_time(ctx):
    return {"now": _now(ctx).strftime("%A %Y-%m-%d %H:%M")}


@tool("check_availability",
      "Check open appointment slots for a given day.",
      {"properties": {"day": {"type": "string",
                              "description": "Day to check, e.g. 'tomorrow' or '2026-07-15'"}},
       "required": ["day"]})
def check_availability(ctx, day):
    now = _now(ctx)
    base = now + timedelta(days=1 if day.strip().lower() == "tomorrow" else 0)
    if day.strip().lower() not in ("today", "tomorrow"):
        try:
            parsed = datetime.strptime(day.strip(), "%Y-%m-%d")
            base = parsed.replace(tzinfo=now.tzinfo) if now.tzinfo else parsed
        except ValueError:
            pass
    hours = ctx["manifest"].get("business", {}).get("slot_hours", [9, 10, 11, 13, 14, 15, 16])
    taken = {a["start"] for a in ctx["memory"].appointments()}
    slots = []
    for h in hours:
        slot_dt = base.replace(hour=h, minute=0, second=0, microsecond=0)
        if slot_dt <= now:  # accuracy: never offer a slot in the past
            continue
        s = slot_dt.strftime("%Y-%m-%d %H:%M")
        if s not in taken:
            slots.append(s)
    return {"day": base.strftime("%Y-%m-%d"), "available_slots": slots[:5],
            "note": "no slots left this day" if not slots else None}


@tool("book_appointment",
      "Book an appointment. Requires customer name, phone, start time and service.",
      {"properties": {"name": {"type": "string"}, "phone": {"type": "string"},
                      "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
                      "service": {"type": "string"}},
       "required": ["name", "phone", "start", "service"]})
def book_appointment(ctx, name, phone, start, service):
    # Idempotency: a retried/duplicated call must never double-book.
    existing = ctx["memory"].find_booking(phone, start)
    if existing:
        return {"booked": True, "id": existing, "name": name, "phone": phone,
                "start": start, "service": service,
                "note": "already booked — returned existing appointment"}
    appt_id = ctx["memory"].book(name, phone, start, service)
    return {"booked": True, "id": appt_id, "name": name, "phone": phone,
            "start": start, "service": service}


@tool("cancel_appointment",
      "Cancel an existing appointment by its id.",
      {"properties": {"appointment_id": {"type": "integer"}},
       "required": ["appointment_id"]})
def cancel_appointment(ctx, appointment_id):
    ctx["memory"].cancel(appointment_id)
    return {"cancelled": True, "id": appointment_id}


@tool("take_message",
      "Record a message for the business owner when the request can't be handled now.",
      {"properties": {"name": {"type": "string"}, "phone": {"type": "string"},
                      "body": {"type": "string"}},
       "required": ["name", "phone", "body"]})
def take_message(ctx, name, phone, body):
    msg_id = ctx["memory"].take_message(name, phone, body)
    return {"message_id": msg_id, "status": "recorded"}


@tool("remember_fact",
      "Save an important durable fact about this customer/session for future "
      "conversations (preferences, recurring issues, commitments made). Use "
      "for things that matter beyond today.",
      {"properties": {"fact": {"type": "string",
                               "description": "one concise, self-contained fact"}},
       "required": ["fact"]})
def remember_fact(ctx, fact):
    ctx["memory"].remember(ctx.get("session", "default"), fact)
    return {"remembered": fact[:100]}


@tool("schedule_task",
      "Schedule work for the future: follow-ups, chasing, reminders, reports. "
      "The task text is executed by the swarm at the given time.",
      {"properties": {"minutes_from_now": {"type": "number"},
                      "task": {"type": "string",
                               "description": "self-contained instruction, e.g. "
                               "'Send Maria (469-555-0143) a reminder about her "
                               "unpaid invoice #204'"}},
       "required": ["minutes_from_now", "task"]})
def schedule_task(ctx, minutes_from_now, task):
    import time as _t
    run_at = _t.time() + float(minutes_from_now) * 60
    job_id = ctx["memory"].schedule_job(run_at, task)
    return {"scheduled": True, "job_id": job_id,
            "runs_in_minutes": minutes_from_now}


@tool("http_request",
      "Call an external HTTP API (GET or POST with JSON). Only domains "
      "allowlisted in this client's manifest are permitted.",
      {"properties": {"method": {"type": "string", "enum": ["GET", "POST"]},
                      "url": {"type": "string"},
                      "json_body": {"type": "string",
                                    "description": "JSON string for POST body"}},
       "required": ["method", "url"]})
def http_request(ctx, method, url, json_body=None):
    from urllib.error import HTTPError
    from urllib.parse import urlparse
    from urllib.request import HTTPRedirectHandler, Request, build_opener
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": f"scheme '{parsed.scheme}' not allowed"}
    allowed = (ctx["manifest"].get("integrations", {}) or {}).get("allowed_domains", [])
    host = parsed.hostname or ""
    if not any(host == d or host.endswith("." + d) for d in allowed):
        return {"error": f"domain '{host}' not in this client's allowed_domains"}

    class NoRedirect(HTTPRedirectHandler):
        # SSRF guard: an allowlisted server must not redirect us elsewhere.
        def redirect_request(self, *a, **k):
            return None

    try:
        data = json_body.encode() if json_body else None
        req = Request(url, data=data, method=method,
                      headers={"Content-Type": "application/json"} if data else {})
        with build_opener(NoRedirect()).open(req, timeout=15) as r:
            body = r.read(20000).decode(errors="replace")
        return {"status": r.status, "body": body}
    except HTTPError as e:
        if 300 <= e.code < 400:
            return {"error": f"redirect blocked (HTTP {e.code}) — redirects are "
                             "not followed for safety"}
        return {"status": e.code, "body": e.read(2000).decode(errors="replace")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


@tool("save_file",
      "Save text content to a file in this client's workspace (drafts, "
      "reports, exports).",
      {"properties": {"filename": {"type": "string"}, "content": {"type": "string"}},
       "required": ["filename", "content"]})
def save_file(ctx, filename, content):
    base = os.path.join(ctx["client_dir"], "workspace")
    os.makedirs(base, exist_ok=True)
    safe = os.path.basename(filename)  # no path escapes
    path = os.path.join(base, safe)
    with open(path, "w") as f:
        f.write(content)
    return {"saved": safe, "bytes": len(content)}


@tool("read_file",
      "Read a text file from this client's workspace (uploaded docs, prior "
      "drafts, data files).",
      {"properties": {"filename": {"type": "string"}},
       "required": ["filename"]})
def read_file(ctx, filename):
    base = os.path.join(ctx["client_dir"], "workspace")
    path = os.path.join(base, os.path.basename(filename))
    if not os.path.exists(path):
        try:
            files = sorted(os.listdir(base))
        except OSError:
            files = []
        return {"error": "not found", "available": files}
    with open(path, errors="replace") as f:
        return {"filename": os.path.basename(filename), "content": f.read(50000)}


@tool("delegate",
      "Delegate a sub-task to a specialist agent. Available specialists: "
      "extractor (structured data from messy text), classifier (triage/label), "
      "drafter (write emails/SMS in the business voice), chaser (follow-up "
      "sequences), summarizer (condense material). Give the specialist ALL "
      "context it needs in the task text.",
      {"properties": {"agent": {"type": "string",
                                "description": "specialist name, e.g. 'extractor'"},
                      "task": {"type": "string",
                               "description": "the full task, self-contained"}},
       "required": ["agent", "task"]})
def delegate(ctx, agent, task):
    from .subagents import run_specialist
    return run_specialist(agent, task, ctx)


@tool("list_appointments",
      "List all currently booked appointments (owner/admin use).",
      {"properties": {}})
def list_appointments(ctx):
    return {"appointments": ctx["memory"].appointments()}
