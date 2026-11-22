"""Copy legacy SQLite state to PostgreSQL; leave originals untouched."""
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.postgres_state import state_pool

def main():
    pool = state_pool()
    path = Path(__file__).resolve().parents[1] / "data" / "summary_cache.sqlite3"
    matched = missing = changed = 0
    try:
        if path.exists():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as legacy, pool.connection() as conn:
                for key, value in legacy.execute("SELECT key,value FROM summaries"):
                    row = conn.execute("SELECT value FROM document_cache WHERE key=%s", (key,)).fetchone()
                    if row is None:
                        missing += 1
                    elif row[0] == json.loads(value):
                        matched += 1
                    else:
                        changed += 1
        print(f"Cache verification: {matched} identical, {missing} missing, {changed} different (PostgreSQL values preserved).")
        with pool.connection() as conn:
            print("PostgreSQL cache records:", conn.execute("SELECT count(*) FROM document_cache").fetchone()[0])
        if missing:
            raise SystemExit("Verification failed: missing cache records.")
    finally:
        pool.close()

if __name__ == "__main__":
    main()
