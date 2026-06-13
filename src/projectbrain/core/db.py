import sqlite3
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from .config import env
from .types import MemRow
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("db")
logger.setLevel(logging.INFO)

class DB:
    def __init__(self):
        self.conn = None
        self.is_postgres = False
        self._last_rowcount = 0

    def connect(self):
        if self.conn:
            return
        url = env.database_url
        if url.startswith("sqlite:///"):
            path = Path(url.replace("sqlite:///", ""))
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"[DB] Connecting to SQLite: {path}")
            self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=-8000")
            self.conn.execute("PRAGMA foreign_keys=OFF")
            self.is_postgres = False
        elif url.startswith("postgresql://") or url.startswith("postgres://"):
            logger.info(f"[DB] Connecting to PostgreSQL...")
            self.conn = psycopg2.connect(url)
            self.conn.autocommit = True
            self.is_postgres = True
        else:
            raise ValueError(f"Unsupported database URL schema: {url}")

        self.run_migrations()

    def run_migrations(self):
        c = self.conn
        if self.is_postgres:
            with c.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER)")
        else:
            c.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER)")

        files = []
        try:
            from importlib import resources
            files = [p.name for p in resources.files('projectbrain.migrations').iterdir() if p.name.endswith(".sql")]
        except (ImportError, TypeError, AttributeError):
            import os
            mig_path = Path(__file__).parent.parent / "migrations"
            if mig_path.exists():
                files = [f for f in os.listdir(mig_path) if f.endswith(".sql")]

        files.sort()

        for f in files:
            already_applied = False
            if self.is_postgres:
                with c.cursor() as cur:
                    cur.execute("SELECT 1 FROM _migrations WHERE name=%s", (f,))
                    already_applied = bool(cur.fetchone())
            else:
                already_applied = bool(self.fetchone("SELECT 1 FROM _migrations WHERE name=?", (f,)))

            if not already_applied:
                logger.info(f"[DB] Applying migration {f}")
                sql = None
                try:
                    from importlib import resources
                    sql = resources.files('projectbrain.migrations').joinpath(f).read_text(encoding='utf-8')
                except:
                    pass
                if not sql:
                    sql = (Path(__file__).parent.parent / "migrations" / f).read_text(encoding="utf-8")

                if self.is_postgres:
                    # Adapt SQLite schema to Postgres
                    sql = sql.replace("AUTOINCREMENT", "")
                    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                    sql = sql.replace("BLOB", "BYTEA")
                    sql = sql.replace("REAL", "DOUBLE PRECISION")
                    # Postgres doesn't support "FOREIGN KEY(id) REFERENCES memories(id)" without type matches,
                    # but memories.id is TEXT, vectors.id is TEXT, so it matches.
                    with c.cursor() as cur:
                        try:
                            cur.execute(sql)
                            cur.execute("INSERT INTO _migrations (name, applied_at) VALUES (%s, %s)", (f, int(time.time())))
                        except Exception as e:
                            logger.error(f"[DB] PostgreSQL Migration {f} failed: {e}")
                            raise e
                else:
                    try:
                        c.executescript(sql)
                        c.execute("INSERT INTO _migrations (name, applied_at) VALUES (?, ?)", (f, int(time.time())))
                    except Exception as e:
                        logger.error(f"[DB] SQLite Migration {f} failed: {e}")
                        raise e

    def init_schema(self):
         self.run_migrations()

    def translate_query(self, sql: str) -> str:
        if not self.is_postgres:
            return sql
        
        # 1. Replace placeholder ? with %s
        sql = sql.replace("?", "%s")
        
        # 2. Replace SQLite-specific INSERT OR IGNORE / INSERT OR REPLACE
        if "INSERT OR IGNORE INTO users" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO users", "INSERT INTO users")
            sql += " ON CONFLICT (user_id) DO NOTHING"
        
        elif "INSERT OR REPLACE INTO waypoints" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO waypoints", "INSERT INTO waypoints")
            sql += " ON CONFLICT (src_id, dst_id) DO UPDATE SET weight=EXCLUDED.weight, updated_at=EXCLUDED.updated_at"

        elif "INSERT OR REPLACE INTO vectors" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO vectors", "INSERT INTO vectors")
            sql += " ON CONFLICT (id, sector) DO UPDATE SET user_id=EXCLUDED.user_id, v=EXCLUDED.v, dim=EXCLUDED.dim"

        elif "INSERT OR REPLACE INTO memories" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO memories", "INSERT INTO memories")
            sql += """ ON CONFLICT (id) DO UPDATE SET 
                user_id=EXCLUDED.user_id, segment=EXCLUDED.segment, content=EXCLUDED.content, 
                simhash=EXCLUDED.simhash, primary_sector=EXCLUDED.primary_sector, tags=EXCLUDED.tags, 
                meta=EXCLUDED.meta, created_at=EXCLUDED.created_at, updated_at=EXCLUDED.updated_at, 
                last_seen_at=EXCLUDED.last_seen_at, salience=EXCLUDED.salience, decay_lambda=EXCLUDED.decay_lambda, 
                version=EXCLUDED.version, mean_dim=EXCLUDED.mean_dim, mean_vec=EXCLUDED.mean_vec, 
                compressed_vec=EXCLUDED.compressed_vec, feedback_score=EXCLUDED.feedback_score"""

        return sql

    def execute(self, sql: str, params: tuple = ()):
        self.connect()
        sql = self.translate_query(sql)
        if self.is_postgres:
            with self.conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                self._last_rowcount = cur.rowcount
        else:
            res = self.conn.execute(sql, params)
            self._last_rowcount = res.rowcount
            return res

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.connect()
        sql = self.translate_query(sql)
        if self.is_postgres:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                self._last_rowcount = cur.rowcount
                return [dict(r) for r in cur.fetchall()]
        else:
            rows = self.conn.execute(sql, params).fetchall()
            self._last_rowcount = len(rows)
            return [dict(r) for r in rows]

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        self.connect()
        sql = self.translate_query(sql)
        if self.is_postgres:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                self._last_rowcount = cur.rowcount
                r = cur.fetchone()
                return dict(r) if r else None
        else:
            r = self.conn.execute(sql, params).fetchone()
            self._last_rowcount = 1 if r else 0
            return dict(r) if r else None

    def commit(self):
        if self.conn:
            self.conn.commit()

    @property
    def total_changes(self) -> int:
        if self.is_postgres:
            return self._last_rowcount
        return self.conn.total_changes if self.conn else 0

db = DB()

class Queries:
    def ins_mem(self, **k):
        sql = """
        INSERT INTO memories(id, user_id, segment, content, simhash, primary_sector, tags, meta, created_at, updated_at, last_seen_at, salience, decay_lambda, version, mean_dim, mean_vec, compressed_vec, feedback_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        user_id=excluded.user_id, segment=excluded.segment, content=excluded.content, simhash=excluded.simhash, primary_sector=excluded.primary_sector,
        tags=excluded.tags, meta=excluded.meta, created_at=excluded.created_at, updated_at=excluded.updated_at, last_seen_at=excluded.last_seen_at,
        salience=excluded.salience, decay_lambda=excluded.decay_lambda, version=excluded.version, mean_dim=excluded.mean_dim,
        mean_vec=excluded.mean_vec, compressed_vec=excluded.compressed_vec, feedback_score=excluded.feedback_score
        """
        vals = (
            k.get("id"), k.get("user_id"), k.get("segment", 0), k.get("content"), k.get("simhash"),
            k.get("primary_sector"), k.get("tags"), k.get("meta"), k.get("created_at"), k.get("updated_at"),
            k.get("last_seen_at"), k.get("salience", 1.0), k.get("decay_lambda", 0.02), k.get("version", 1),
            k.get("mean_dim"), k.get("mean_vec"), k.get("compressed_vec"), k.get("feedback_score", 0)
        )
        db.execute(sql, vals)
        db.commit()

    def get_mem(self, mid: str):
        return db.fetchone("SELECT * FROM memories WHERE id=?", (mid,))

    def all_mem(self, limit=10, offset=0):
        return db.fetchall("SELECT * FROM memories ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))

    def ins_log(self, id: str, model: str, status: str, ts: int, err: Optional[str] = None):
        db.execute("INSERT INTO embed_logs(id, model, status, ts, err) VALUES (?,?,?,?,?)", (id, model, status, ts, err))
        db.commit()

    def upd_log(self, id: str, status: str, err: Optional[str] = None):
        db.execute("UPDATE embed_logs SET status=?, err=? WHERE id=?", (status, err, id))
        db.commit()

    def all_mem_by_user(self, user_id: str, limit=10, offset=0):
        return db.fetchall("SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (user_id, limit, offset))

    def get_waypoints_by_src(self, src_id: str):
        return db.fetchall("SELECT * FROM waypoints WHERE src_id=?", (src_id,))

    def del_mem(self, mid: str):
        db.execute("DELETE FROM memories WHERE id=?", (mid,))
        db.execute("DELETE FROM vectors WHERE id=?", (mid,))
        db.execute("DELETE FROM waypoints WHERE src_id=? OR dst_id=?", (mid, mid))
        db.commit()

    def del_mem_by_user(self, uid: str):
        db.execute("DELETE FROM vectors WHERE id IN (SELECT id FROM memories WHERE user_id=?)", (uid,))
        db.execute("DELETE FROM waypoints WHERE src_id IN (SELECT id FROM memories WHERE user_id=?) OR dst_id IN (SELECT id FROM memories WHERE user_id=?)", (uid, uid))
        db.execute("DELETE FROM memories WHERE user_id=?", (uid,))
        db.commit()

q = Queries()

def transaction():
    return db.conn
