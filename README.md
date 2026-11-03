# Self-Healing Data Pipeline

An autonomous, resilient data ingestion platform that detects, diagnoses, and remediates upstream schema drift in real time using **LangGraph**, **FastAPI**, **Neon Serverless Postgres**, and **React**.

---

## The Problem

In modern data architectures, upstream data contracts break unexpectedly:
* A vendor renames a column (`customer_id` → `acct_ref` or `total_amount` → `amount`).
* Numeric values arrive formatted as strings with commas or currency symbols.
* Unexpected nulls appear in non-nullable fields.

Traditional ETL pipelines fail immediately, trigger urgent off-hours alerts, and require manual data engineering intervention to patch code and backfill tables.

**Self-Healing Data Pipeline** turns ingestion into an adaptive state machine: it diagnoses the drift using specialized LLM agents, synthesizes bounded declarative schema patches, verifies fixes through self-healing cycles, pauses for human review on ambiguous drift, and commits all changes with full database-level transactional safety.

---

## Key Capabilities

* **Autonomous Self-Healing:** Resolves column renames, type casting mismatches, null policies, and date formats without modifying backend code or restarting services.
* **Declarative Safety Guardrails:** Healing agents output strictly validated Pydantic configuration schemas (`rename`, `casts`, `fill_na`, `dates`), **never arbitrary Python code or raw SQL**.
* **Durable Human-in-the-Loop (HITL):** When patch confidence is below threshold ($< 0.80$), LangGraph halts at an `interrupt()` node, durably serializing state to Postgres until an operator approves or rejects it in the UI.
* **Compare-And-Swap (CAS) Concurrency & Rollbacks:** Mapping updates use PostgreSQL advisory transaction locks (`pg_advisory_xact_lock`) with parent version checks. Conflicting or failed downstream runs trigger automated rollbacks to the prior state.
* **Dynamic Data Contract Registry:** Operators can upload, validate, and version YAML/JSON data contracts at runtime without modifying the underlying repository.
* **Real-Time Observability:** Interactive dashboard displays graph execution timelines, schema diffs, patch comparisons, confidence distributions, and specialist performance.

---

## Architecture Overview

```mermaid
flowchart TD
    A["Raw Ingestion (CSV / Source)"] --> B["Validate against Data Contract"]
    B -- "Passed (Clean)" --> C["Commit Report & Ingest Data"]
    B -- "Failed (Drift Detected)" --> D["Profile Source & Diagnose Root Cause"]
    
    D --> E{"Drift Classification"}
    E -- "Column Rename" --> F1["Rename Specialist"]
    E -- "Type Mismatch" --> F2["Type Specialist"]
    E -- "Null Values" --> F3["Null Policy Specialist"]
    E -- "Multiple Drifts" --> F4["Multi-Specialist Coordinator"]
    E -- "Unknown / Unfixable" --> X["Escalate (Terminal Failure)"]
    
    F1 & F2 & F3 & F4 --> G["Synthesize Declarative Patch"]
    G --> H{"Confidence Check"}
    
    H -- "Confidence >= 0.80" --> I["Apply In-Memory Patch & Re-validate"]
    H -- "Confidence < 0.80" --> J["Durable Pause: Human Review"]
    
    J -- "Approved" --> I
    J -- "Rejected" --> X
    
    I -- "Assertions Passed" --> K["CAS Commit Mapping to Postgres"]
    I -- "Assertions Failed (< 2 attempts)" --> D
    I -- "Assertions Failed (>= 2 attempts)" --> L["Auto-Rollback CAS & Escalate"]
    K --> C
```

---

## Technology Stack

| Layer | Component | Description |
| :--- | :--- | :--- |
| **Agent Orchestration** | [LangGraph](https://github.com/langchain-ai/langgraph) | Cyclic, stateful graph execution with durable checkpointer storage. |
| **Backend API** | FastAPI / Python 3.12+ | Asynchronous REST service with Pydantic validation. |
| **LLM Inference** | Groq (`llama-3.3-70b-versatile`) | Ultra-fast inference with strict JSON schema outputs. |
| **Database** | Neon Serverless Postgres | Relational data, mapping ledger, and LangGraph checkpointer storage. |
| **Data Engine** | Pandas | High-speed vectorized validation assertions and patch execution. |
| **Frontend** | React 19 + Vite + Tailwind CSS | Interactive dashboard with live run polling, graph visualization, and approvals. |
| **State Management**| Zustand | Reactive client stores for runs, analytics, and authentication. |

---

## Quickstart & Local Development

### Prerequisites
* Python 3.12+ (or 3.14 with virtual environment)
* Node.js 20+ / npm
* A [Neon Postgres](https://neon.tech) database URL (or local PostgreSQL)
* A [Groq API Key](https://console.groq.com)

### 1. Environment Setup
Create a `.env` file in the project root:
```bash
cp .env.example .env
```
Fill in your credentials:
```ini
DATABASE_URL=postgresql://user:password@ep-xyz.region.aws.neon.tech/neondb?sslmode=require
SHARED_TOKEN=your-random-24-character-hex-token
GROQ_API_KEY=gsk_your_groq_api_key
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
APP_ENV=local
PIPELINE_ENV=dashboard-demo
```

### 2. Backend Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt -r requirements-dev.txt

# Start backend server
cd backend
python run_local.py 8000
```
> **Note for Windows users:** Always run `python run_local.py` instead of bare `uvicorn`. It installs the required `SelectorEventLoop` for asynchronous `psycopg 3` socket operations.

### 3. Frontend Setup
In a new terminal:
```bash
cd frontend
cp .env.example .env.local
```
In `frontend/.env.local`:
```ini
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SHARED_TOKEN=your-random-24-character-hex-token
```
Install dependencies and launch the dev server:
```bash
npm install
npm run dev
```
Open **`http://localhost:5173`** in your browser.

---

## Core API Reference

| Endpoint | Method | Auth | Description |
| :--- | :--- | :--- | :--- |
| `/health` | `GET` | None | Liveness probe (does not touch database). |
| `/api/ping` | `GET` | Bearer | Proves authenticated database round-trip. |
| `/api/runs` | `GET` | Bearer | Lists pipeline runs across all contracts or filtered by `contract_name`. |
| `/api/runs` | `POST` | Bearer | Triggers an ingestion run for a source dataset against a contract. |
| `/api/runs/{id}` | `GET` | Bearer | Retrieves full graph state, assertion results, schema diffs, and patch details. |
| `/api/runs/{id}/approve` | `POST` | Bearer | Resumes a paused run from `human_approval` and applies proposed patch. |
| `/api/runs/{id}/reject` | `POST` | Bearer | Rejects proposed patch and terminates run with `escalated` status. |
| `/api/config/contracts` | `GET` | Bearer | Enumerates active data contracts and registered schemas. |
| `/api/config/mapping` | `GET` | Bearer | Retrieves current active schema mapping version. |
| `/api/config/mapping/history` | `GET` | Bearer | Returns full audit trail of mapping versions and rollback events. |
| `/api/app/datasets` | `POST` | Bearer | Uploads a CSV dataset (capped at 5 MB / 5,000 rows). |

---

## Running Tests

The test suite contains 302 unit, integration, and regression tests:

```bash
# Run complete test suite
pytest -o pythonpath=. -q

# Run with coverage report
pytest -o pythonpath=. --cov=backend
```

---

## Documentation & Deep Dives

* [Architecture & Specification](ARCHITECTURE.md) — Detailed technical design and rubric mapping.
* [Knowledge Transfer (KT) Manual](docs/knowledge_transfer.md) — Exhaustive guide covering state definitions, safety guardrails, CAS locking, and common FAQs.
* [Presentation Script & Demo Guide](docs/presentation_script.md) — 15-minute presentation script and step-by-step walkthrough for live demonstrations.
* [Synthetic Fault Datasets](data/synthetic_tests/README.md) — Guide to testing clean, renamed, type-drifted, and escalated cases.
