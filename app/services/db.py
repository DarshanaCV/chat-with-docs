import os
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool, AsyncConnectionPool

load_dotenv()

from app.services.visitor_scope import DEMO_MODE

POSTGRES_CONN_STRING = os.getenv("POSTGRES_CONN_STRING")
if DEMO_MODE:
    demo_connection = os.getenv("DEMO_POSTGRES_CONN_STRING")
    if not demo_connection or demo_connection == POSTGRES_CONN_STRING:
        raise RuntimeError("Demo mode requires a separate DEMO_POSTGRES_CONN_STRING")
    POSTGRES_CONN_STRING = demo_connection

pool = ConnectionPool(
    conninfo=POSTGRES_CONN_STRING,
    max_size=20,
    kwargs={"autocommit": True, "prepare_threshold": 0},
)

async_pool = AsyncConnectionPool(
    conninfo=POSTGRES_CONN_STRING,
    max_size=20,
    kwargs={"autocommit": True, "prepare_threshold": 0},
    open=False,
)
