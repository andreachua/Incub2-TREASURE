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

Register + review (what the frontend calls):
- `GET /api/summary` — home tiles: per-CAT counts, pending count, last-updated.
- `GET /api/categories?system=oxn` — MINDEF Category options from the category YAML.
- `GET /api/assets?search=&limit=&offset=` — the register; top-level rows with
  their components nested. `search` matches tag no., serial no., model, vendor.
- `GET /api/review/latest`, `GET /api/review/{job_id}` — one record as review-form
  fields, each tagged with its provenance (`ai` / `system` / `manual`), plus the
  classification rationale and the source-document list.
- `GET /api/jobs/{job_id}/status` — `{status: queued|ready|error, stage}`. Always
  200, so a client can poll an in-flight upload without generating 404 noise.
  While `status` is `queued`, `stage` says where in the pipeline the job is —
  one of `extract`, `context`, `enrich`, `categorize`, `write`
  (`app/pipeline/orchestrator.py`: `STAGES`), or `""` before the consumer picks
  the job up. The consumer records it per job under `assets:progress:<job_id>`;
  the upload screen renders it as a checklist so a two-minute run is legible.
- `POST /api/review/{job_id}/complete` — persist a reviewed record. The reviewed
  line item becomes the asset (`status="Registered"`); the other line items from
  the same invoice are linked to it as components (`parent_no`).
- `GET /api/review/blank` — an empty registration form for adding an asset by
  hand. All 23 fields come back marked `manual`, which falls out of the existing
  provenance rules rather than being a special case.
- `POST /api/assets` — add an asset. With a `job_id` this is a completed review
  (components are linked as above); without one the record was typed in by hand
  and is inserted directly, carrying no `job_id` so it never shows up as pending.

Source documents (what the review screen's rail links to):
- `GET /api/documents/receipt/{receipt_id}` — the uploaded invoice's own bytes,
  served with the stored content type. For a PDF, `?as=png&page=N` renders that
  page (pdf2image + poppler, already in the image) so a viewer never depends on
  a browser PDF plugin.
- `GET /api/documents/receipt/{receipt_id}/info` — `{filename, content_type,
  is_pdf, pages, size}`; what a viewer needs before it fetches.
- `GET /api/documents/po/{ref_no}`, `GET /api/documents/sow/{ref_no}` — the
  purchase order(s) and scope of work filed under a PO reference, as
  `{ref_no, documents: [{filename, category, content}]}`.

Each `source_documents` entry in a review payload carries a `kind`
(`invoice`/`po`/`sow`/`do`) and the `url` that serves it — empty when the
reference was extracted but nothing is held for it. A review payload also carries
`context_match`: which purchase order the crawler tied the invoice to, how
confident it is, and the evidence, which the rail renders under the PO entry.

## Matching an invoice to its purchase order

`app/agents/document_crawler.py` answers "which contract is this invoice
against?" It used to be one exact lookup on the digits the VLM read off the page;
a single misread digit meant no PO or SOW context at all, and every downstream
step quietly degraded. Now there is a ladder:

| rung | how | cost |
|---|---|---|
| 0 | the reference matches exactly | no model call |
| 1 | a deepagents agent with five tools | one local round trip |
| 2 | one non-tool call with the whole index | for models without tool-calling |
| 3 | pure-Python signal scoring | no model at all |
| 4 | no match — the pipeline behaves as it did before | — |

Rung 0 covers the common case, so nothing that already worked got slower. **Rung
3 is the load-bearing one** and was built first: fuzzy reference + vendor +
payment-event amount identifies an order in this corpus with no model involved.

The agent gets five narrow tools and never writes SQL — the whole corpus renders
as ~2.5 KB of cards, so there is nothing to search that it cannot read. Nothing
returns a document body; the agent picks a reference and code loads the text.
Any reference it returns is checked against the database and dropped if absent,
and confidence is recomputed from signals that verify rather than taken from the
model. Below `PO_CRAWLER_MIN_CONFIDENCE` the answer is "no match": a wrong
purchase order is worse than none.

`PO_CRAWLER=off` restores the old exact-match behaviour; `deterministic` skips
both model rungs. Run `tests/test_crawler.py` (needs Postgres, not the model) for
the cases this exists for, including two that must be refusals.

The searchable index behind it is built by `app/ingest/documents.py` from
`po.content`/`sow.content` already in Postgres — deterministic regex
(`app/ingest/parse.py`, verified 25/25 on every field), so it needs no model
endpoint and takes under a second.

## Serving the frontend

`GET /app` serves the exported design bundle from `frontend/` with
`<script src="/static/bridge.js">` injected **into the response**. The HTML file
on disk is never modified — it is a read-only Claude-Design export whose fields
are React-controlled and inert on their own. `static/bridge.js` fills the form
and the register from the API above, makes the fields editable, wires the search
box, the Complete-review button and a document upload, and re-applies itself
after every React re-render. Add `?mardebug=1` for bridge logging and a
`window.__mar` handle.

Set `MAR_FRONTEND_DIR` to point at the bundle directory (the compose file mounts
`./frontend` read-only and sets it for the container).

This route and its bridge are the original design export, kept as-is. The
operator front end now in use is the Streamlit app in
`frontend/streamlit_app/` — the same screens, styles and workflow, ported off
the export and talking to the `/api/*` routes above directly. Compose builds it
as the `frontend` service on `127.0.0.1:8501`; it calls the API by service name
(`API_BASE=http://api:8000`), so the browser only ever talks to that container.

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
