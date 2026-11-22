"""Persistent checkpoints, keyed by exact input, model and summary format version."""
import hashlib
import json

# Keep the original namespace so saved cache records remain usable.
CACHE_NAMESPACE = "openai/gpt-oss-120b"


class SummaryCache:
    def __init__(self, *, pool=None):
        from app.services.postgres_state import state_pool
        self.pool = pool if pool is not None else state_pool()

    @staticmethod
    def key(kind, value):
        serialized = json.dumps(["summary-v2", CACHE_NAMESPACE, kind, value], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, kind, value):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT value FROM document_cache WHERE key=%s", (self.key(kind, value),)).fetchone()
        return row[0] if row else None

    def put(self, kind, value, result):
        from psycopg.types.json import Jsonb
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO document_cache VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (self.key(kind, value), Jsonb(result)))
