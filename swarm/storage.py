"""Storage backends — swap databases with one env var, zero code changes.

  SWARM_DB_URL unset            → SQLite (default; one file per client)
  SWARM_DB_URL=postgres://...   → PostgreSQL (mid-market scale; needs psycopg2)

Contract every backend implements:
  query(sql, params)  → list[dict]           (SELECTs)
  execute(sql, params) → rowcount            (UPDATE/DELETE/DDL)
  insert(sql, params) → new row id           (INSERTs)
SQL is written ONCE in memory.py using '?' placeholders and portable types;
backends translate. All schema DDL lives here, per backend, so a migration
is: set the env var, run once, done.

Postgres isolation: run ONE database per client (the same one-folder-one-world
rule). Point each client's deployment at its own SWARM_DB_URL.
"""
import os
import sqlite3
import threading

SCHEMA = [
    ("appointments",
     "id {PK}, name TEXT, phone TEXT, start TEXT, service TEXT, "
     "status TEXT DEFAULT 'booked', created {REAL}"),
    ("messages", "id {PK}, name TEXT, phone TEXT, body TEXT, created {REAL}"),
    ("history", "id {PK}, session TEXT, role TEXT, content TEXT, created {REAL}"),
    ("events", "id {PK}, actor TEXT, tool TEXT, args TEXT, result TEXT, created {REAL}"),
    ("jobs", "id {PK}, run_at {REAL}, task TEXT, session TEXT, status TEXT, created {REAL}"),
    ("notes", "id {PK}, session TEXT, fact TEXT, created {REAL}"),
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hist ON history(session, id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs ON jobs(status, run_at)",
    "CREATE INDEX IF NOT EXISTS idx_notes ON notes(session, id)",
    "CREATE INDEX IF NOT EXISTS idx_appt ON appointments(phone, start, status)",
]


class SqliteBackend:
    PK, REAL = "INTEGER PRIMARY KEY", "REAL"

    def __init__(self, client_dir):
        override = os.environ.get("SWARM_DB_DIR")
        db_dir = (os.path.join(override, os.path.basename(client_dir.rstrip("/")))
                  if override else os.path.join(client_dir, "db"))
        os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(os.path.join(db_dir, "swarm.db"),
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()  # one writer at a time, thread-safe
        for name, cols in SCHEMA:
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS {name}("
                              + cols.format(PK=self.PK, REAL=self.REAL) + ")")
        for idx in INDEXES:
            self.conn.execute(idx)
        self.conn.commit()

    def query(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.rowcount

    def insert(self, sql, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid


class PostgresBackend:
    """Mid-market backend. Same contract; '?' placeholders are translated to
    '%s'. Requires: pip install psycopg2-binary. One database per client."""
    PK, REAL = "SERIAL PRIMARY KEY", "DOUBLE PRECISION"

    def __init__(self, url):
        import psycopg2
        import psycopg2.extras
        self._pg = psycopg2
        self._extras = psycopg2.extras
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = True
        self._lock = threading.Lock()
        with self.conn.cursor() as c:
            for name, cols in SCHEMA:
                c.execute(f"CREATE TABLE IF NOT EXISTS {name}("
                          + cols.format(PK=self.PK, REAL=self.REAL) + ")")
            for idx in INDEXES:
                c.execute(idx)

    @staticmethod
    def _tr(sql):
        return sql.replace("?", "%s")

    def query(self, sql, params=()):
        with self._lock, self.conn.cursor(
                cursor_factory=self._extras.RealDictCursor) as c:
            c.execute(self._tr(sql), params)
            return [dict(r) for r in c.fetchall()]

    def execute(self, sql, params=()):
        with self._lock, self.conn.cursor() as c:
            c.execute(self._tr(sql), params)
            return c.rowcount

    def insert(self, sql, params=()):
        with self._lock, self.conn.cursor() as c:
            c.execute(self._tr(sql) + " RETURNING id", params)
            return c.fetchone()[0]


def get_storage(client_dir):
    url = os.environ.get("SWARM_DB_URL", "")
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresBackend(url)
    return SqliteBackend(client_dir)
