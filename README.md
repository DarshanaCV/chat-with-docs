## Chat With Docs

Upload a PDF, then ask questions about its text, tables, and figures.

### Start locally

You need Python 3.14+ with [uv](https://docs.astral.sh/uv/), Node.js, and a
PostgreSQL database. Create an empty database and configure the environment
below before starting the app.

1. In one PowerShell terminal, from the project root, start the FastAPI backend:

   ```powershell
   uv run uvicorn app.main:app --reload --port 8000
   ```

   Wait for `Application startup complete` while the backend initializes the
   database tables and loads its models. You can then open
   `http://127.0.0.1:8000/docs`.

2. In a second PowerShell terminal, start the frontend:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

3. Open `http://localhost:3000`, upload a PDF, and ask a question.

The frontend proxies `/api` to `http://127.0.0.1:8000` by default. Set
`API_PROXY_TARGET` in `frontend/.env.local` and restart Next.js to change the
backend. Remove any older `NEXT_PUBLIC_API_URL` override to use this proxy.

### Required configuration

Copy the root `.env.example` to `.env`. Set `POSTGRES_CONN_STRING` to your
PostgreSQL connection string and `GEMINI_API_KEY` to your Gemini API key.
Keep the real values in your local environment or your host's secret settings.

### Public demo database and browser sessions

1. Provision an empty PostgreSQL database on the demo host or through a hosted
   provider such as Neon. Use a separate role and the direct PostgreSQL
   connection string. Keep the local app's database on its existing connection.
2. On the backend host, set `APP_MODE=demo`, `DEMO_POSTGRES_CONN_STRING`, and
   `GEMINI_API_KEY`. Use a separate working directory or deployment volume for
   `data/`. On startup, the demo creates its tables and skips importing old
   SQLite caches. The API and its document subprocess use the same demo database.
3. On the Next.js host, set `API_PROXY_TARGET` to the backend URL. Leave
   `NEXT_PUBLIC_API_URL` unset so API calls and citation images use the HTTPS
   frontend origin. Serve the deployed frontend over HTTPS so the browser
   accepts the visitor cookie.
4. From two separate browser profiles, create conversations and attach a
   sample file in each. Refresh both profiles and confirm that each sees only
   its own conversations and can open its own citation images.

Demo sessions are anonymous and tied to a browser cookie that expires 30 days
after it is issued. Clearing it starts a fresh visitor session. Cookie expiry
does not delete server-side chats or files.
