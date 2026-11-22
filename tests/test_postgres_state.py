"""Opt-in PostgreSQL migration and concurrent admission tests in a disposable schema."""
import json
import os
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from dotenv import load_dotenv
from app.services.postgres_state import initialize, admit
from app.services.summary_cache import SummaryCache

load_dotenv()
pytestmark = pytest.mark.skipif(os.getenv("RUN_STATE_DB_TESTS") != "1", reason="Set RUN_STATE_DB_TESTS=1 for PostgreSQL integration")

@pytest.fixture
def database(tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg_pool import ConnectionPool
    schema = "test_state_" + uuid.uuid4().hex
    conninfo = os.environ["POSTGRES_CONN_STRING"]
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with ConnectionPool(conninfo, min_size=1, max_size=8,
                kwargs={"autocommit": True, "options": f"-c search_path={schema}", "connect_timeout": 5}) as pool:
                yield pool, tmp_path
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

def test_migration_preserves_all_values_and_does_not_overwrite_new_data(database):
    pool, path = database
    key = SummaryCache.key("document-preparation-v1", "doc")
    value = {"text_ready": True, "pages": [{"figure_captions": [{"figure_number": "2"}]}]}
    with sqlite3.connect(path / "summary_cache.sqlite3") as legacy:
        legacy.execute("CREATE TABLE summaries(key TEXT PRIMARY KEY,value TEXT)")
        legacy.execute("INSERT INTO summaries VALUES (?,?)", (key,json.dumps(value)))
    initialize(pool,path)
    assert SummaryCache(pool=pool).get("document-preparation-v1","doc") == value
    SummaryCache(pool=pool).put("document-preparation-v1","doc",{"new": True})
    initialize(pool,path)
    assert SummaryCache(pool=pool).get("document-preparation-v1","doc") == {"new": True}
    with sqlite3.connect(path / "summary_cache.sqlite3") as legacy:
        assert json.loads(legacy.execute("SELECT value FROM summaries").fetchone()[0]) == value

def test_concurrent_admission_does_not_exceed_budget(database):
    pool, path = database
    initialize(pool,path)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: admit(pool,"model",60,100,10),range(6)))
    assert results.count(None) == 1
    with pool.connection() as conn:
        conn.execute("UPDATE gemini_usage SET at=now()-interval '61 seconds'")
    assert admit(pool,"model",60,100,10) is None
    assert admit(pool,"other-model",60,100,1) is None
    assert admit(pool,"other-model",0,100,1) is not None

def test_active_legacy_usage_is_imported_once(database):
    pool, path = database
    with sqlite3.connect(path / "gemini_usage.sqlite3") as legacy:
        legacy.execute("CREATE TABLE usage(model TEXT,at REAL,tokens INTEGER)")
        legacy.executemany("INSERT INTO usage VALUES (?,?,?)", [("m",time.time(),80),("m",time.time()-120,80)])
    initialize(pool,path)
    initialize(pool,path)
    assert admit(pool,"m",30,100,10) is not None
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM gemini_usage").fetchone()[0] == 1

def test_bad_legacy_data_rolls_back_then_can_retry(database):
    pool, path = database
    with sqlite3.connect(path / "summary_cache.sqlite3") as legacy:
        legacy.execute("CREATE TABLE summaries(key TEXT,value TEXT)")
        legacy.executemany("INSERT INTO summaries VALUES (?,?)",[("good","{}"),("bad","not json")])
    with pytest.raises(json.JSONDecodeError):
        initialize(pool,path)
    with pool.connection() as conn:
        assert conn.execute("SELECT to_regclass('document_cache')").fetchone()[0] is None
    with sqlite3.connect(path / "summary_cache.sqlite3") as legacy:
        legacy.execute("UPDATE summaries SET value='{}' WHERE key='bad'")
    initialize(pool,path)
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM document_cache").fetchone()[0] == 2
