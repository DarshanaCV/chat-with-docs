"""PostgreSQL cache/rate-limit storage and transactional legacy SQLite import."""
import json
import logging
import sqlite3
import threading
from pathlib import Path
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_ready = False

def initialize(pool, legacy_dir=None, *, import_legacy=True):
    legacy_dir = Path(legacy_dir or Path(__file__).resolve().parents[2] / "data")
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(724108314)")
        conn.execute("CREATE TABLE IF NOT EXISTS document_cache (key TEXT PRIMARY KEY, value JSONB NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_state_migrations (name TEXT PRIMARY KEY, completed_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        conn.execute("CREATE TABLE IF NOT EXISTS gemini_rate_scopes (model TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS gemini_usage (model TEXT NOT NULL, at TIMESTAMPTZ NOT NULL, tokens BIGINT NOT NULL CHECK(tokens >= 0))")
        conn.execute("CREATE INDEX IF NOT EXISTS gemini_usage_model_at ON gemini_usage(model, at)")
        if not import_legacy:
            return
        marker = "sqlite-state-v1:" + str(legacy_dir.resolve())
        if conn.execute("SELECT 1 FROM app_state_migrations WHERE name=%s", (marker,)).fetchone():
            return
        count = 0
        cache_file = legacy_dir / "summary_cache.sqlite3"
        if cache_file.exists():
            with sqlite3.connect(cache_file.resolve().as_uri() + "?mode=ro", uri=True) as legacy:
                for key, value in legacy.execute("SELECT key,value FROM summaries"):
                    conn.execute("INSERT INTO document_cache VALUES (%s,%s) ON CONFLICT(key) DO NOTHING", (key, Jsonb(json.loads(value))))
                    count += 1
        usage_file = legacy_dir / "gemini_usage.sqlite3"
        if usage_file.exists():
            with sqlite3.connect(usage_file.resolve().as_uri() + "?mode=ro", uri=True) as legacy:
                for model, at, tokens in legacy.execute("SELECT model,at,tokens FROM usage"):
                    conn.execute("INSERT INTO gemini_usage SELECT %s,to_timestamp(%s),%s WHERE to_timestamp(%s)>now()-interval '60 seconds'", (model, at, tokens, at))
        conn.execute("INSERT INTO app_state_migrations(name) VALUES (%s)", (marker,))
        logger.info("PostgreSQL storage ready | imported %d legacy cache records; SQLite originals retained", count)

def state_pool():
    global _ready
    from app.services.db import pool
    from app.services.visitor_scope import DEMO_MODE
    with _lock:
        if not _ready:
            initialize(pool, import_legacy=not DEMO_MODE)
            _ready = True
    return pool

def admit(pool, model, tokens, limit, rpm):
    """Atomic admission across processes; return wait seconds or None on success."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("INSERT INTO gemini_rate_scopes VALUES (%s) ON CONFLICT DO NOTHING", (model,))
        conn.execute("SELECT model FROM gemini_rate_scopes WHERE model=%s FOR UPDATE", (model,))
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        conn.execute("DELETE FROM gemini_usage WHERE model=%s AND at<=%s-interval '60 seconds'", (model, now))
        count, used, oldest = conn.execute("SELECT count(*),coalesce(sum(tokens),0),min(at) FROM gemini_usage WHERE model=%s", (model,)).fetchone()
        if count < rpm and used + tokens <= limit:
            conn.execute("INSERT INTO gemini_usage VALUES (%s,%s,%s)", (model, now, tokens))
            return None
        return max(.1, 60 - (now-oldest).total_seconds())
