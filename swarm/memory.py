"""Per-client memory. All SQL lives HERE and only here, written portably
('?' placeholders, common types); the backend (storage.py) translates.
Swap SQLite → Postgres with SWARM_DB_URL — no code changes anywhere.

Callers outside this file must use these methods (or Memory.query for
read-only reporting) — never a raw connection.
"""
import time

from .storage import get_storage


class Memory:
    def __init__(self, client_dir):
        self.db = get_storage(client_dir)
        # Back-compat handle for sqlite-era tests; None on other backends.
        self.conn = getattr(self.db, "conn", None) \
            if self.db.__class__.__name__ == "SqliteBackend" else None

    # -- generic read-only reporting (dashboard, --status) --
    def query(self, sql, params=()):
        return self.db.query(sql, params)

    # -- conversation history --
    def add_history(self, session, role, content):
        self.db.insert("INSERT INTO history(session, role, content, created) "
                       "VALUES(?,?,?,?)", (session, role, content, time.time()))

    def get_history(self, session, limit=20):
        rows = self.db.query(
            "SELECT role, content FROM history WHERE session=? "
            "ORDER BY id DESC LIMIT ?", (session, limit))
        return [{"role": r["role"], "content": r["content"]}
                for r in reversed(rows)]

    def history_count(self, session):
        return self.db.query("SELECT COUNT(*) AS n FROM history WHERE session=?",
                             (session,))[0]["n"]

    def trim_history(self, session, keep_last):
        self.db.execute(
            "DELETE FROM history WHERE session=? AND id NOT IN ("
            "SELECT id FROM history WHERE session=? ORDER BY id DESC LIMIT ?)",
            (session, session, keep_last))

    # -- appointments --
    def book(self, name, phone, start, service):
        return self.db.insert(
            "INSERT INTO appointments(name, phone, start, service, created) "
            "VALUES(?,?,?,?,?)", (name, phone, start, service, time.time()))

    def find_booking(self, phone, start):
        rows = self.db.query(
            "SELECT id FROM appointments WHERE phone=? AND start=? "
            "AND status='booked'", (phone, start))
        return rows[0]["id"] if rows else None

    def appointments(self):
        return self.db.query(
            "SELECT * FROM appointments WHERE status='booked' ORDER BY start")

    def cancel(self, appointment_id):
        self.db.execute("UPDATE appointments SET status='cancelled' WHERE id=?",
                        (appointment_id,))

    # -- messages --
    def take_message(self, name, phone, body):
        return self.db.insert(
            "INSERT INTO messages(name, phone, body, created) VALUES(?,?,?,?)",
            (name, phone, body, time.time()))

    def recent_messages(self, limit=25):
        return self.db.query(
            "SELECT name, phone, body, created FROM messages "
            "ORDER BY id DESC LIMIT ?", (limit,))

    # -- long-term memory --
    def remember(self, session, fact):
        self.db.insert("INSERT INTO notes(session, fact, created) VALUES(?,?,?)",
                       (session, fact[:500], time.time()))

    def recall(self, session, limit=15):
        rows = self.db.query(
            "SELECT fact FROM notes WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, limit))
        return [r["fact"] for r in reversed(rows)]

    # -- scheduled jobs --
    def schedule_job(self, run_at, task, session="scheduler"):
        return self.db.insert(
            "INSERT INTO jobs(run_at, task, session, status, created) "
            "VALUES(?,?,?,?,?)", (run_at, task, session, "pending", time.time()))

    def due_jobs(self, now=None):
        return self.db.query(
            "SELECT * FROM jobs WHERE status='pending' AND run_at<=? "
            "ORDER BY run_at", (now or time.time(),))

    def pending_jobs_count(self):
        return self.db.query(
            "SELECT COUNT(*) AS n FROM jobs WHERE status='pending'")[0]["n"]

    def recent_jobs(self, limit=25):
        return self.db.query("SELECT id, run_at, task, status FROM jobs "
                             "ORDER BY id DESC LIMIT ?", (limit,))

    def finish_job(self, job_id, status="done"):
        self.db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))

    # -- audit trail --
    def log_event(self, actor, tool, args, result):
        self.db.insert(
            "INSERT INTO events(actor, tool, args, result, created) "
            "VALUES(?,?,?,?,?)",
            (actor, tool, str(args)[:500], str(result)[:500], time.time()))

    def recent_events(self, limit=60):
        return self.db.query(
            "SELECT actor, tool, args, result, created FROM events "
            "ORDER BY id DESC LIMIT ?", (limit,))

    def last_route(self):
        rows = self.db.query("SELECT result FROM events WHERE tool='route' "
                             "ORDER BY id DESC LIMIT 1")
        return rows[0]["result"] if rows else None

    def event_count(self):
        return self.db.query("SELECT COUNT(*) AS n FROM events")[0]["n"]

    def usage_turns(self):
        return self.db.query(
            "SELECT COUNT(*) AS n FROM events WHERE tool='usage'")[0]["n"]
