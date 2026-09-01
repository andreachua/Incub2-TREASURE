# Asset Management Extraction Pipeline (backend)

Turns an uploaded receipt/invoice (image or PDF) into a structured asset register:
`name`, `description`, `category`, `group`, `quantity`, `price` per line item.

## Architecture

```
frontend --POST /receipts (file + system)--> FastAPI (api.py)
  -> store receipt in pgvector (receipts table) -> receipt_id
  -> LPUSH Redis message {job_id, system, receipt_id} onto "object-categorization"
  -> 201 Created {job_id, receipt_id, system, status}

Stage 1 consumer (src/stage1_extractor.py: run_consumer)
  -> BRPOP "object-categorization"
  -> fetch receipt from pgvector by receipt_id
  -> Stage 1: VLM extraction  ->  Stage 2: categorize (system's category set)
  -> publish result: SET assets:result:<job_id>, PUBLISH assets:results, output/<job_id>.json
```

Two **systems** select the category set (processing is identical):
- `oxn` -> `config/categories.oxn.yaml`
- `ehab` -> `config/categories.ehab.yaml` (placeholder until provided)

## Prerequisites
- [`uv`](https://docs.astral.sh/uv/), Docker, and `poppler-utils` (PDF input: `sudo apt-get install -y poppler-utils`).
- A local OpenAI-compatible VLM endpoint (default `http://192.168.100.1:5000/v1`).

## Setup
```bash
uv sync                     # install deps (Python 3.12)
cp .env.example .env         # edit LLM_MODEL / endpoint / DB / Redis as needed

# Postgres + pgvector (docker-compose v1 is incompatible with this Docker; run directly):
docker run -d --name assets-pgvector \
  -e POSTGRES_DB=assets -e POSTGRES_USER=assets -e POSTGRES_PASSWORD=assets \
  -p 5432:5432 -v assets_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16

# Redis (job queue):
docker run -d --name assets-redis -p 6379:6379 redis:7-alpine
```

## Configure the model
The model id must match what the endpoint serves (`GET /v1/models`). In `.env`:
```
LLM_MODEL=gemma-4-31B-it      # change to Qwen3.8-27B (etc.) when loaded
LLM_BASE_URL=http://192.168.100.1:5000/v1
```

## Run with Docker (recommended)
The API server and the pipeline consumer run as containers from one image
(`backend/Dockerfile`); `docker-compose.yml` also brings up Postgres + Redis.
```bash
docker compose up -d --build      # db, redis, api (:8000), pipeline consumer
curl -F "file=@backend/samples/receipt1.png" -F "system=oxn" http://localhost:8000/receipts
```
The `api` service's entrypoint is the API server; the `pipeline` service reuses
the image and overrides the entrypoint to run `python -m src.stage1_extractor`.
`LLM_BASE_URL` / `LLM_MODEL` come from the repo `.env` (or shell) — make sure the
containers can reach your local VLM endpoint.

## Run locally (without Docker)
```bash
# 1. API server (receives uploads, enqueues jobs)
uv run uvicorn api:app --host 0.0.0.0 --port 8000

# 2. Pipeline consumer (Stage 1 entry point; consumes "object-categorization")
uv run python -m src.stage1_extractor

# 3. Upload a receipt (frontend does this)
curl -F "file=@samples/receipt1.png" -F "system=oxn" http://localhost:8000/receipts
#   -> 201 {job_id, receipt_id, system, status:"queued"}
# Poll the result:
curl http://localhost:8000/results/<job_id>
```

> The optional embeddings/vector path (torch) is not installed by default or in
> the image. Enable it locally with `uv sync --extra embeddings`.

CLI (no queue, for quick local runs):
```bash
uv run main.py --receipt samples/receipt1.png --system oxn --out output/assets.json
```

Local queue helper (enqueue without the API): `uv run scripts/enqueue.py --system oxn --receipt samples/receipt1.png --wait`

## Categories
Edit `config/categories.<system>.yaml` (name, group, code, description). The agent is constrained to those
names; the `group` (Assets/Inventories/Expenses) is derived from the chosen category. Changes take effect on
the next run — no code change.

## Endpoints
- `POST /receipts` — multipart `file` + form `system` (oxn|ehab) -> 201 `{job_id, receipt_id, system, status}`.
- `GET /results/{job_id}` — the published pipeline result (404 until ready).
- `GET /health` — `{status, db, redis}`.

## Layout
```
api.py                      FastAPI upload server (enqueues to "object-categorization")
main.py                     CLI orchestrator (no queue)
config/categories.oxn.yaml  oxn categories (from categories.txt)
config/categories.ehab.yaml ehab categories (placeholder)
src/stage1_extractor.py     VLM extraction + Redis consumer (pipeline entry point)
src/stage2_categorizer.py   deepagents agent (+ deterministic fallback)
src/stage3_writer.py        JSON output shape
src/store/                  DocumentStore interface + Postgres/pgvector (receipts + project_documents)
scripts/enqueue.py          enqueue a job onto the queue (testing)
scripts/seed_db.py          seed mock project_documents (legacy; unused by categorization now)
```
