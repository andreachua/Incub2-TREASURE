# Asset Management Extraction Pipeline — Design

## Context

**Problem:** Teams register assets by hand — reading receipts/invoices/purchase records, deciding what to
register, classifying items consistently, and keeping records accurate. This project automates that: a
receipt/invoice is uploaded, and a pipeline extracts line items, classifies each into an asset taxonomy, and
writes a structured asset register to Postgres.

**Outcome:** For each uploaded receipt, one row per line item is written to the Postgres `mar` table with
`asset_model`, `description`, `quantity`, `price`, `main_category`, `sub_category` (and columns reserved for
`asset_id`, `serial_no`, `location`, `status`).

## Key decisions

- **Async, queue-driven.** A FastAPI server accepts the upload and returns **201** immediately; the heavy work
  runs in a separate consumer, decoupled via **Redis**.
- **Two systems: `oxn` and `ehab`.** The system is chosen per request and travels in the Redis message. It
  selects the **category set only** — processing is identical. `oxn` categories come from `categories.txt`;
  `ehab` categories are a placeholder until provided.
- **Local, offline models.** Stage 1 (VLM extraction) and Stage 2 (deepagents categorization agent) both call
  one **local OpenAI-compatible endpoint** — no external API keys. Model id is env-configurable
  (`LLM_MODEL`); the server currently serves `gemma-4-31B-it` (set to `Qwen3.8-27B` when loaded).
- **Inputs:** images (jpg/png/…) and PDFs.
- **Storage:** PostgreSQL + pgvector — stores uploaded receipts (`receipts`) and the final register (`mar`).
- **Environment:** Python **3.12** via `uv`; Postgres + Redis via Docker.

---

## Architecture

```
                          ┌──────────────────────────────────────────┐
  frontend  ──upload──▶   │ FastAPI  (backend/api.py)                 │
  (file + system)         │   POST /receipts                          │
                          │   1. store file in pgvector → receipt_id  │
                          │   2. LPUSH {job_id, system, receipt_id}   │
                          │      onto Redis "object-categorization"   │
                          │   3. 201 {job_id, receipt_id, system}     │
                          └───────────────┬──────────────────────────┘
                                          │ Redis queue
                                          ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │ Stage 1 consumer  (backend/src/stage1_extractor.py: run_consumer)  │
        │   BRPOP "object-categorization"                                    │
        │   fetch receipt bytes from pgvector by receipt_id                  │
        │                                                                    │
        │   Stage 1  extract_line_items() ── local VLM (image/PDF→JSON)      │
        │        │   → List[RawLineItem]{name, description, quantity, price} │
        │        ▼                                                           │
        │   Stage 2  categorize(items, system) ── deepagents agent          │
        │        │   tool: list_categories() (system's set); assigns one     │
        │        │   category per item; group derived deterministically      │
        │        ▼   → List[AssetRecord]{…, category, group}                 │
        │   Stage 3  write_assets_to_db() → INSERT into Postgres "mar"       │
        │                                                                    │
        │   publish result: SET assets:result:<job_id>,                     │
        │                   PUBLISH assets:results, output/<job_id>.json     │
        └──────────────────────────────────────────────────────────────────┘

  frontend polls  GET /results/{job_id}  ->  {status, count, records, mar_ids}
```

Stage 1 is the **pipeline entry point**: it consumes the Redis message and drives Stages 2 and 3.
`backend/main.py` is a CLI that runs the same three stages locally without the queue.

---

## Repository layout

```
Incub2/
├── PLAN.md                     # this document
├── categories.txt              # source-of-truth policy taxonomy (oxn)
├── docker-compose.yml          # pgvector + redis (Compose v2)
├── .env / .env.example
├── data/                       # sample PO / invoice / SOW markdown docs
├── frontend/                   # (to be built) — talks to the API
└── backend/
    ├── pyproject.toml / uv.lock          # uv-managed (Python 3.12)
    ├── api.py                            # FastAPI upload server
    ├── main.py                           # CLI orchestrator (--receipt --system)
    ├── config/
    │   ├── categories.oxn.yaml           # oxn taxonomy (from categories.txt)
    │   └── categories.ehab.yaml          # ehab taxonomy (placeholder)
    ├── src/
    │   ├── models.py                     # RawLineItem, AssetRecord, Category
    │   ├── config.py                     # settings + per-system category loader
    │   ├── llm.py                         # OpenAI + ChatOpenAI factories (local endpoint)
    │   ├── stage1_extractor.py           # VLM extraction + Redis consumer (entry point)
    │   ├── stage2_categorizer.py         # deepagents agent (+ deterministic fallback)
    │   ├── stage3_writer.py              # write to Postgres "mar" (+ JSON helper)
    │   ├── embeddings.py                 # optional local embeddings (unused by default)
    │   └── store/{base.py, postgres_store.py}
    └── scripts/
        ├── enqueue.py                    # push a job onto the queue (testing)
        └── seed_db.py                    # seed mock project_documents (legacy)
```

---

## Systems & categories

- `system ∈ {oxn, ehab}`, chosen at upload and carried in the Redis message.
- `backend/config/categories.<system>.yaml` — editable list; each entry has `name`, `group`
  (`Assets` / `Inventories` / `Expenses`), `code` (policy letter a–j), `description`.
- The agent is constrained to the `name` values; the `group` is **derived** from the chosen category (not
  trusted from the model). Editing the YAML changes behavior on the next run — no code change.
- `oxn` is populated from `categories.txt` (8 Asset categories a–h, 1 Inventory i, 1 Expense j).
  `ehab` is an empty placeholder until its categories are supplied.

## Data model

- `RawLineItem{name, description="", quantity=1.0, price=0.0}` — Stage 1 output (tolerant numeric coercion).
- `AssetRecord{name, description, category, group, quantity, price}` — Stage 2 output.
- Result payload (Redis/API): `{job_id, system, status, count, records[], mar_ids[], error}`.

## Database (PostgreSQL + pgvector)

```sql
-- uploaded receipts/invoices (bytes)
CREATE TABLE receipts (
    id SERIAL PRIMARY KEY, system TEXT NOT NULL,
    filename TEXT, content_type TEXT, data BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- final asset register (Stage 3 output)
CREATE TABLE mar (
    "No" SERIAL PRIMARY KEY,
    asset_id TEXT, asset_model TEXT, serial_no TEXT, description TEXT,
    quantity NUMERIC, location TEXT, status TEXT, price NUMERIC,
    main_category TEXT, sub_category TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

`mar` mapping: `asset_model←name`, `description←description`, `quantity`/`price` as extracted,
`main_category←group`, `sub_category←category`. `asset_id`/`serial_no`/`location`/`status` are NULL (not
derivable from a receipt yet). Tables are created via `CREATE TABLE IF NOT EXISTS` on API startup and
defensively before the first `mar` insert. (`project_documents` + `embeddings.py` remain from an earlier
project-document retrieval design and are currently unused by the pipeline.)

## HTTP API (`backend/api.py`)

- `POST /receipts` — multipart `file` + form `system` (`oxn`|`ehab`) → **201**
  `{job_id, receipt_id, system, status:"queued"}`; invalid system → 422.
- `GET /results/{job_id}` — the published result (404 until ready).
- `GET /health` — `{status, db, redis}`.

## Configuration (`.env`)

`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; `POSTGRES_*`; `REDIS_URL`,
`REDIS_JOB_QUEUE=object-categorization`, `REDIS_RESULT_PREFIX`, `REDIS_RESULTS_CHANNEL`, `REDIS_RESULT_TTL`.

---

## Run & verify

```bash
# infra
docker run -d --name assets-pgvector -e POSTGRES_DB=assets -e POSTGRES_USER=assets \
  -e POSTGRES_PASSWORD=assets -p 5432:5432 -v assets_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
docker run -d --name assets-redis -p 6379:6379 redis:7-alpine

cd backend
uv sync                                        # Python 3.12 deps
sudo apt-get install -y poppler-utils          # PDF support

uv run uvicorn api:app --host 0.0.0.0 --port 8000    # 1. API
uv run python -m src.stage1_extractor                 # 2. pipeline consumer

# 3. upload (frontend does this)
curl -F "file=@samples/receipt1.png" -F "system=oxn" http://localhost:8000/receipts
curl http://localhost:8000/results/<job_id>
# verify:  SELECT "No", asset_model, sub_category, main_category, quantity, price FROM mar;
```

CLI (no queue): `uv run main.py --receipt samples/receipt1.png --system oxn`.

## Notes / risks

- The model id must match `GET /v1/models` on the endpoint (`LLM_MODEL`); today it serves `gemma-4-31B-it`.
- deepagents drives the local model via a LangChain `ChatOpenAI`; a deterministic single-call fallback handles
  models without tool-calling. Local-model JSON is parsed tolerantly.
- `project_id` and project-document retrieval were removed — descriptions now come solely from the receipt
  (no DB enrichment). A non-`project_id` context source could restore enrichment later.
- `"No"` is a quoted, case-sensitive identifier (queries must quote it). The `ehab` taxonomy is empty until
  provided. `frontend/` is not built yet; `data/` docs are not wired into the pipeline.
- The bundled `docker-compose` v1.29.2 is incompatible with the installed Docker (`http+docker` bug); run the
  containers directly (as above) or use Compose v2.
