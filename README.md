# Self-Healing Data Pipeline

> Existing tools — Great Expectations, Monte Carlo, dbt tests — *detect* data drift and alert.
> A human still writes the patch. This agent closes the remaining loop: it diagnoses the drift,
> proposes a remediation patch, and escalates to a human when confidence is low rather than
> silently mutating production logic. We measure how many injected faults it heals unaided,
> how many it correctly escalates, and how many it misses.

A LangGraph agent that repairs a data pipeline when upstream data drifts. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full design — it is the source of truth for this build.

---

## Build status

| Phase | Scope | Status |
|---|---|---|
| **0** | Deployment skeleton — public frontend to public backend, bearer auth, Neon | **verified locally; awaiting cloud provisioning** |
| 1 | Pipeline + contract + fault datasets | not started |
| 2 | Graph skeleton with stub nodes | not started |
| 3 | Real nodes (LLM) | not started |
| 4 | Interrupt + Postgres checkpointer | not started |
| 5 | Dashboard | not started |
| 6 | Eval, docs, freeze | not started |

Phase 0 contains **no pipeline code and no LangGraph usage** by design. `langgraph` is installed
and its version recorded, but nothing imports it yet.

---

## Recorded versions

Verified against the installed packages, not against documentation.

| Package | Version |
|---|---|
| **langgraph** | **1.2.11** |
| langgraph-checkpoint | 4.2.0 |
| langgraph-checkpoint-postgres | 3.1.2 |
| langchain-core | 1.6.0 |
| fastapi | 0.141.1 |
| uvicorn | 0.52.4 |
| pydantic | 2.13.4 |
| psycopg | 3.3.4 |
| pandas | 3.0.5 |
| pandera | 0.32.1 |
| Python | 3.14 (local 3.14.3 / container 3.14.7) |
| Postgres (Neon) | 18.6 |

### LangGraph API verification

ARCHITECTURE.md warns that its import paths may be stale. They were checked against 1.2.11 and
**all still resolve**: `langgraph.graph.{StateGraph,START,END}`,
`langgraph.types.{interrupt,Command}`, `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`.

Behaviour was verified by running a real graph, not just by importing names:

- `interrupt(value)` halts; `Command(resume=...)` resumes **at the interrupted node**, not at START.
- `Annotated[list, operator.add]` appends across resumes (`[{attempt: 1}, {attempt: 2}]`).
- A resumed node **re-executes from the top of its function body**, so `human_approval` must have
  no side effects before its `interrupt()` call.

Two findings that change how the code must be written:

1. **`AsyncPostgresSaver.from_conn_string()` is an async context manager** and closes its
   connection on block exit. A checkpointer opened that way cannot resume a graph across HTTP
   requests. The pool is therefore owned by the FastAPI lifespan ([backend/db.py](backend/db.py))
   and the checkpointer will be built around it in Phase 4.
2. **uvicorn hard-codes `ProactorEventLoop` on Windows** via a loop *factory*, which ignores
   `asyncio.set_event_loop_policy()`. psycopg's async mode refuses that loop, so a bare
   `uvicorn main:app` on Windows starts fine and then every DB call times out with no clear cause.
   Local Windows dev must use [backend/run_local.py](backend/run_local.py). The Linux container
   uses uvloop and is unaffected.

### Deviation from ARCHITECTURE.md

The document's `contract.yaml` declares `region: { dtype: object }`. On pandas 3, a string column
infers as `str`, not `object`, so that contract would fail on `day1_clean.csv` — the control case —
and spuriously trigger the heal loop. The contract will use `str`. Keeping pandas 3 also keeps the
local and container runtimes identical, which matters more on a deploy-first schedule than matching
the document verbatim. Also note `pandera.pandas` is the current import namespace.

---

## Local development

Requires Python 3.14, Node 24, and a Neon connection string.

```bash
# secrets
cp .env.example .env          # then fill in DATABASE_URL and SHARED_TOKEN
                              # token: python -c "import secrets;print(secrets.token_hex(24))"

# backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd backend && ../.venv/Scripts/python.exe run_local.py 8123
```

Use `run_local.py`, not `uvicorn`, on Windows — see the event-loop note above.

```bash
# frontend
cd frontend
cp .env.example .env.local    # set VITE_API_BASE_URL + VITE_SHARED_TOKEN
npm install
npm run dev                   # http://localhost:5173
```

The frontend renders a Phase 0 status page that runs all four Definition-of-Done checks in the
browser, including a deliberately unauthenticated request that must be rejected with 401.

### Running the container locally

```bash
cd backend && docker build -t shp-backend:phase0 .
docker run --rm -p 8199:10000 -e PORT=10000 \
  -e DATABASE_URL="..." -e SHARED_TOKEN="..." \
  -e CORS_ORIGINS="http://localhost:5173" -e APP_ENV=container \
  shp-backend:phase0
```

---

## API (Phase 0)

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness. Deliberately does **not** touch the DB, so a suspended Neon compute cannot make the deploy look unhealthy. |
| `GET /` | none | Service banner. |
| `GET /api/ping` | Bearer | Authenticated round-trip. Writes a row to `deploy_probe` and reports the Postgres version plus recorded package versions. |

All `/api/*` routes require `Authorization: Bearer <SHARED_TOKEN>`; anything else returns 401.

---

## Deployment

Backend on Render (Docker), frontend on Vercel, Postgres on Neon.

**Render** — either apply [render.yaml](render.yaml) as a blueprint, or create a Docker web
service manually with:

- Dockerfile path `./backend/Dockerfile`, Docker context `./backend`
- Health check path `/health`
- Env vars: `DATABASE_URL`, `SHARED_TOKEN`, `CORS_ORIGINS`, `APP_ENV=render`

**Vercel** — import the repo and set **Root Directory to `frontend`** (the repo root is not the
frontend). Framework preset Vite. Env vars `VITE_API_BASE_URL` (the Render URL, no trailing slash)
and `VITE_SHARED_TOKEN`.

Then add the real Vercel origin to `CORS_ORIGINS` on Render and redeploy.

### Operational caveats

- **Render free tier sleeps** after ~15 min and takes ~50s to wake. Warm it before a live demo.
- **Neon free tier suspends** its compute when idle; the first connection can exceed 30s. The pool
  is opened with `wait=False` so the app boots immediately rather than blocking on a cold compute.
- **Render's filesystem is ephemeral.** The mapping config lives in Postgres from Phase 1 onward;
  `config/mapping.yaml` is only a seed.

---

## Known limitations

- `VITE_SHARED_TOKEN` is inlined into the client bundle, so the shared token is public to anyone
  who loads the page. This is inherent to the single-shared-token design in ARCHITECTURE.md
  (no user accounts, no JWT) and is acceptable only for a demo deployment.
- Further limitations are recorded per phase as the build proceeds.
