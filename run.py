#!/usr/bin/env python3
"""Swarm CLI.

  python run.py --client demo-hvac            chat with a client's swarm
  python run.py --client demo-hvac --test     run the scripted self-test (mock brain)

Environment (see .env.example): SWARM_BASE_URL, SWARM_API_KEY, SWARM_MODEL, SWARM_MOCK
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swarm.orchestrator import Swarm  # noqa: E402


def cli_approve(action, args):
    ans = input(f"\n[GATE] Swarm wants to run '{action}' with {args}. Allow? [y/N] ")
    return ans.strip().lower() == "y"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--client", help="folder name under clients/")
    p.add_argument("--test", action="store_true", help="run scripted self-test with mock brain")
    p.add_argument("--build", metavar="TRANSCRIPT",
                   help="build a new client solution from a discovery transcript file")
    p.add_argument("--name", help="client folder name to create (with --build)")
    p.add_argument("--serve", type=int, metavar="PORT",
                   help="run the universal HTTP API for this client")
    p.add_argument("--work", action="store_true",
                   help="run the scheduler worker loop for this client")
    p.add_argument("--work-once", action="store_true",
                   help="run due scheduled jobs once and exit")
    p.add_argument("--status", action="store_true",
                   help="owner digest: bookings, waiting messages, pending approvals")
    p.add_argument("--company", metavar="OBJECTIVE",
                   help="run an objective through the org hierarchy")
    p.add_argument("--role", metavar="ROLE",
                   help="with --company: enter the org at this role "
                        "(a sub-team instead of the whole company)")
    p.add_argument("--improve", metavar="REQUEST",
                   help="Swarm engineers Swarm: implement a change to its own code "
                        "(staged + validated, never applied automatically)")
    p.add_argument("--apply", action="store_true",
                   help="apply reviewed .staging/ changes to live code")
    args = p.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    if args.improve:
        from swarm.engineer import improve
        r = improve(args.improve, root)
        print(f"Staged files: {r['staged_files']}")
        print(f"Validation passed: {r['validated']}")
        print(f"\n{r['report']}")
        if r["staged_files"]:
            print("\nReview .staging/ then run:  python run.py --apply")
        return
    if args.apply:
        from swarm.engineer import apply_staged
        applied = apply_staged(root)
        print(f"Applied: {applied}" if applied else "Nothing staged.")
        return

    root = os.path.dirname(os.path.abspath(__file__))
    if args.build:
        if not args.name:
            sys.exit("--build requires --name <client-folder-name>")
        if not os.path.exists(args.build):
            sys.exit(f"Transcript file not found: {args.build}")
        from swarm.builder import build_client
        client_dir, report = build_client(args.build, args.name, root)
        print(f"Built client solution at: {client_dir}\n")
        print(report)
        return
    if not args.client:
        sys.exit("Provide --client <name>, or --build <transcript> --name <name>")

    client_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "clients", args.client)
    if not os.path.isdir(client_dir):
        sys.exit(f"No such client folder: {client_dir}")

    if args.test:
        os.environ["SWARM_MOCK"] = "1"
        # fresh db for a deterministic test
        db_root = os.environ.get("SWARM_DB_DIR")
        db = (os.path.join(db_root, os.path.basename(client_dir), "swarm.db")
              if db_root else os.path.join(client_dir, "db", "swarm.db"))
        try:
            if os.path.exists(db):
                os.remove(db)
        except OSError:
            print(f"(warning: could not reset {db}; test may reuse old data)")
        swarm = Swarm(client_dir)
        script = [
            "Hi, my AC stopped blowing cold air, can someone come out?",
            "My name is John, number is 972-555-1234 — tomorrow works, "
            "book me for an AC repair.",
            "Also tell the tech the unit is on the roof, call me back if that matters",
            "One more thing — our bookkeeper says you're missing our March bank "
            "statement, can you chase that down?",
        ]
        for line in script:
            print(f"\nCALLER: {line}")
            print(f"SWARM : {swarm.handle(line, session='test')}")
        appts = swarm.memory.appointments()
        assert appts, "TEST FAILED: no appointment was booked"
        a = appts[0]
        assert a["name"] == "John" and "972" in a["phone"], \
            f"TEST FAILED: booked without real contact info: {a}"
        jobs = swarm.memory.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        assert jobs >= 1, "TEST FAILED: back-office chase was not scheduled"
        print(f"\n✅ SELF-TEST PASSED — booked {a['name']} / {a['phone']} / "
              f"{a['start']} ({a['service']}); {jobs} follow-up job(s) scheduled")
        return

    if args.status:
        s = Swarm(client_dir)
        print(f"=== {s.manifest['name']} — status ===\n")
        appts = s.memory.appointments()
        print(f"Booked appointments ({len(appts)}):")
        for a in appts[:20]:
            print(f"  {a['start']}  {a['name']}  {a['phone']}  ({a['service']})")
        rows = s.memory.conn.execute(
            "SELECT name, phone, body FROM messages ORDER BY id DESC LIMIT 20").fetchall()
        approvals = [r for r in rows if r["body"].startswith("APPROVAL NEEDED")]
        others = [r for r in rows if not r["body"].startswith("APPROVAL NEEDED")]
        print(f"\nPending approvals ({len(approvals)}):")
        for r in approvals:
            print(f"  ! {r['body'][:100]}")
        print(f"\nRecent messages ({len(others)}):")
        for r in others:
            print(f"  {r['name']} {r['phone']}: {r['body'][:80]}")
        jobs = s.memory.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        print(f"\nScheduled jobs pending: {jobs}")
        import ast
        usage_rows = s.memory.conn.execute(
            "SELECT result FROM events WHERE tool='usage'").fetchall()
        calls = pt = ct = 0
        for r in usage_rows:
            try:
                d = ast.literal_eval(r["result"])
                calls += d.get("calls", 0)
                pt += d.get("prompt_tokens", 0)
                ct += d.get("completion_tokens", 0)
            except (ValueError, SyntaxError):
                pass
        print(f"LLM cost to date: {calls} calls, {pt} prompt + {ct} completion tokens")
        return
    if args.company:
        from swarm.company import run_company
        r = run_company(args.company, Swarm(client_dir), entry_role=args.role)
        if "error" in r:
            print(f"[{r['role']}] FAILED: {r['error']}")
        else:
            print(f"[{r['role']}] {r['result']}")
        return
    if args.serve:
        from swarm.server import serve
        serve(Swarm(client_dir), args.serve)
        return
    if args.work or args.work_once:
        from swarm.worker import run_due_jobs, work_loop
        s = Swarm(client_dir)
        if args.work_once:
            print(f"ran {run_due_jobs(s)} due job(s)")
        else:
            work_loop(s)
        return

    swarm = Swarm(client_dir, approve_fn=cli_approve)
    print(f"Swarm online for: {swarm.manifest['name']}  (model: {swarm.llm.model})")
    print("Type as the caller. Ctrl-C to quit.\n")
    while True:
        try:
            user = input("CALLER: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nbye")
            break
        if user:
            print(f"SWARM : {swarm.dispatch(user)}")


if __name__ == "__main__":
    main()
