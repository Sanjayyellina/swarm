"""Per-client memory: one SQLite DB per client folder. Strict isolation."""
import os
import sqlite3
import time


class Memory:
    def __init__(self, client_dir):
        # SWARM_DB_DIR overrides where databases live (e.g. fast local disk).
        override = os.environ.get("SWARM_DB_DIR")
        if override:
            db_dir = os.path.join(override, os.path.basename(client_dir.rstrip("/")))
        else:
            db_dir = os.path.join(client_dir, "db")
        os.makedirs(db_dir, exist_ok=True)
        # check_same_thread=False: the API server handles requests in threads;
        # SQLite serializes writes internally and our ops are short.
        self.conn = sqlite3.connect(os.path.join(db_dir, "swarm.db"),
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS appointments(
            id INTEGER PRIMARY KEY, name TEXT, phone TEXT, start TEXT,
            service TEXT, status TEXT DEFAULT 'booked', created REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, name TEXT, phone TEXT, body TEXT, created REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT, created REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, actor TEXT, tool TEXT, args TEXT,
            result TEXT, created REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY, run_at REAL, task TEXT, session TEXT,
            status TEXT, created REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY, session TEXT, fact TEXT, created REAL)""")
        # Performance: indexes for every hot query path.
        c.execute("CREATE INDEX IF NOT EXISTS idx_hist ON history(session, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs ON jobs(status, run_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes ON notes(session, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_appt ON appointments(phone, start, status)")
        c.commit()

    def find_booking(self, phone, start):
        row = self.conn.execute(
            "SELECT id FROM appointments WHERE phone=? AND start=? AND status='booked'",
            (phone, start)).fetchone()
        return row["id"] if row else None

    # -- long-term memory: distilled facts that outlive the history window --
    def remember(self, session, fact):
        self.conn.execute("INSERT INTO notes(session, fact, created) VALUES(?,?,?)",
                          (session, fact[:500], time.time()))
        self.conn.commit()

    def recall(self, session, limit=15):
        rows = self.conn.execute(
            "SELECT fact FROM notes WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, limit)).fetchall()
        return [r["fact"] for r in reversed(rows)]

    def history_count(self, session):
        return self.conn.execute(
            "SELECT COUNT(*) FROM history WHERE session=?", (session,)).fetchone()[0]

    def trim_history(self, session, keep_last):
        """Delete oldest history rows beyond keep_last (after compaction)."""
        self.conn.execute(
            "DELETE FROM history WHERE session=? AND id NOT IN ("
            "SELECT id FROM history WHERE session=? ORDER BY id DESC LIMIT ?)",
            (session, session, keep_last))
        self.conn.commit()

    # -- scheduled jobs (time-driven work: chase, report, remind, monitor) --
    def schedule_job(self, run_at, task, session="scheduler"):
        cur = self.conn.execute(
            "INSERT INTO jobs(run_at, task, session, status, created) VALUES(?,?,?,?,?)",
            (run_at, task, session, "pending", time.time()))
        self.conn.commit()
        return cur.lastrowid

    def due_jobs(self, now=None):
        now = now or time.time()
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM jobs WHERE status='pending' AND run_at<=? ORDER BY run_at",
            (now,)).fetchall()]

    def finish_job(self, job_id, status="done"):
        self.conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        self.conn.commit()

    def log_event(self, actor, tool, args, result):
        """Audit trail: every tool call, who made it, with what, what came back."""
        self.conn.execute(
            "INSERT INTO events(actor, tool, args, result, created) VALUES(?,?,?,?,?)",
            (actor, tool, str(args)[:500], str(result)[:500], time.time()))
        self.conn.commit()

    # -- conversation history --
    def add_history(self, session, role, content):
        self.conn.execute("INSERT INTO history(session, role, content, created) VALUES(?,?,?,?)",
                          (session, role, content, time.time()))
        self.conn.commit()

    def get_history(self, session, limit=20):
        rows = self.conn.execute(
            "SELECT role, content FROM history WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # -- appointments --
    def book(self, name, phone, start, service):
        cur = self.conn.execute(
            "INSERT INTO appointments(name, phone, start, service, created) VALUES(?,?,?,?,?)",
            (name, phone, start, service, time.time()))
        self.conn.commit()
        return cur.lastrowid

    def appointments(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM appointments WHERE status='booked' ORDER BY start").fetchall()]

    def cancel(self, appointment_id):
        self.conn.execute("UPDATE appointments SET status='cancelled' WHERE id=?",
                          (appointment_id,))
        self.conn.commit()

    # -- messages --
    def take_message(self, name, phone, body):
        cur = self.conn.execute(
            "INSERT INTO messages(name, phone, body, created) VALUES(?,?,?,?)",
            (name, phone, body, time.time()))
        self.conn.commit()
        return cur.lastrowid
