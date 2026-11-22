import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging

from app.logging_setup import configure_logging
configure_logging("API")
logging.getLogger(__name__).info("Starting backend; preparing services")

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.upload import router as upload_router
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.visitor import router as visitor_router
from app.services.session_store import init_tables
from app.services.agent import build_agent
from app.services.document_connection import document_session
from app.services.db import async_pool, pool
from app.services.visitor_scope import DEMO_MODE
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_mcp_adapters.tools import load_mcp_tools
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.postgres_state import state_pool
    try:
        await asyncio.to_thread(state_pool)
        async with async_pool:
            checkpointer = AsyncPostgresSaver(async_pool)
            await checkpointer.setup()
            logger.info("Starting document server and loading its models...")
            async with document_session() as session:
                tools = await load_mcp_tools(session)
                app.state.agent = build_agent(tools, checkpointer)
                app.state.checkpointer = checkpointer
                app.state.mcp_session = session
                logger.info(f"Document tools ready; connection will stay open")
                yield
            logger.info("Document server connection closed")
    finally:
        await asyncio.to_thread(pool.close)


app = FastAPI(title="Chat With Docs API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if DEMO_MODE:
    @app.middleware("http")
    async def prevent_shared_caching(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        return response
else:
    app.mount("/data", StaticFiles(directory="data"), name="data")

app.include_router(visitor_router)
app.include_router(upload_router)
app.include_router(sessions_router)
init_tables()
app.include_router(chat_router)

@app.get("/")
def root():
    return {"message": "Chat With Docs API is running"}
