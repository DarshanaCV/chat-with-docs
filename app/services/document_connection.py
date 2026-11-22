"""Keep one MCP process alive and explicitly relay its stderr to the API console."""
import os
import sys
import threading
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def document_session(parameters=None, console=None):
    parameters = parameters or StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_servers.document_server"],
        env={**{name: os.environ[name] for name in (
            "APP_LOG_LEVEL", "APP_LOG_DIR", "APP_MODE", "POSTGRES_CONN_STRING",
            "DEMO_POSTGRES_CONN_STRING", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "GEMINI_SUMMARY_MODEL", "GEMINI_SUMMARY_INPUT_BUDGET", "GEMINI_MODEL",
            "GEMINI_VISION_MODEL", "GEMINI_RPM_LIMIT", "GEMINI_TPM_LIMIT", "GEMINI_QUEUE_TIMEOUT") if name in os.environ},
             "PYTHONIOENCODING": "utf-8"},
    )
    console = console if console is not None else sys.stderr
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
    writer = os.fdopen(write_fd, "w", encoding="utf-8")

    def relay():
        with reader:
            for line in reader:
                console.write(line)
                console.flush()

    thread = threading.Thread(target=relay, name="document-log-relay", daemon=True)
    thread.start()
    try:
        async with stdio_client(parameters, errlog=writer) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    finally:
        writer.close()
        thread.join(timeout=2)
