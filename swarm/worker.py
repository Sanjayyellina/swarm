"""Time-driven work: the scheduler loop.

Agents create future work with the schedule_task tool (follow-ups, chases,
reports, monitors). This worker wakes up, finds due jobs, and feeds each one
back into the swarm as a task. That turns Swarm from reactive (answers when
spoken to) into proactive (acts when it's time) — which is most of the
back-office value.

Run:  python run.py --client X --work           (poll forever)
      python run.py --client X --work-once      (one pass, for cron/tests)
"""
import time


def run_due_jobs(swarm):
    """One pass: execute everything due. Returns number of jobs run."""
    done = 0
    for job in swarm.memory.due_jobs():
        try:
            reply = swarm.handle(
                f"[SCHEDULED TASK — execute now, do not reschedule unless "
                f"instructed] {job['task']}",
                session=job["session"])
            swarm.memory.log_event("scheduler", "run_job",
                                   {"id": job["id"], "task": job["task"][:200]},
                                   reply[:200])
            swarm.memory.finish_job(job["id"], "done")
        except Exception as e:  # noqa: BLE001 — one bad job can't stop the rest
            swarm.memory.finish_job(job["id"], "failed")
            swarm.memory.take_message(
                "system", "n/a",
                f"SCHEDULED JOB {job['id']} FAILED: {job['task'][:200]} — {e}")
        done += 1
    return done


def work_loop(swarm, interval=30):
    print(f"Swarm worker for '{swarm.manifest['name']}' — polling every {interval}s")
    while True:
        n = run_due_jobs(swarm)
        if n:
            print(f"ran {n} job(s)")
        time.sleep(interval)
