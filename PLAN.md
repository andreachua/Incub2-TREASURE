# Asset Management Extraction Pipeline — Implementation Plan

## Context

**Why:** Teams currently register assets by hand — reading receipts/purchase records, deciding what to
register, classifying items, and keeping records accurate. This hackathon project automates that with a
3-stage pipeline that turns a receipt (image or PDF) + a `project_id` into a clean structured asset record.

**Intended outcome:** Given a receipt file and a `project_id`, produce a JSON file with, per line item:
`name`, `description`, `category`, `quantity`, `price`.

**Confirmed decisions:**
- **Stage 1 extraction** — call a **local OpenAI-compatible VLM**. Endpoint `http://192.168.100.1:5000`,
  vision-capable (send the receipt image directly). Model id is env-configurable via `LLM_MODEL`
  (the running server currently serves `gemma-4-31B-it`; set to `Qwen3.8-27B` when that model is loaded).
- **Inputs** — **both images and PDFs**.
- **Stage 2** — a **deepagents agent** (only Stage 2 is agentic; Stages 1 & 3 are deterministic Python).
  The agent uses the **same local model** (fully local/offline, no external API keys).
- **Database** — **PostgreSQL + pgvector**. Primary retrieval is a **key lookup by `project_id`** that returns
  the full document; the vector column is an optional shortlisting booster. Behind a swappable interface.
- **Line-item matching** — the **agent (LLM) reasons** over the fetched document to pick the most related
  line item.
- **Categories** — mock, IT-related, editable (`config/categories.yaml`).

**Environment:** Python **3.12** (managed by `uv`). Package management: `uv` (`uv add` / `pyproject.toml` +
`uv.lock`; run via `uv run`). Postgres via `docker-compose` (standalone v1.29.2).

---

## Architecture

```
receipt (jpg/png/pdf) + project_id
        │
        ▼
Stage 1: Extraction (deterministic)      src/stage1_extractor.py
   • PDF -> page images (pdf2image); images passed through
   • OpenAI-compatible vision call to the local endpoint
   • returns List[RawLineItem]{name, description, quantity, price}
        │
        ▼
Stage 2: Categorization (deepagents agent, same local model)   src/stage2_categorizer.py
   Tools: list_categories()            <- categories.yaml
          get_project_document(pid)    <- Postgres key lookup by project_id
          search_line_items(pid, q)    <- (optional) pgvector shortlist
   • agent assigns category + enriches description from best line item
        │
        ▼
Stage 3: Output (deterministic)          src/stage3_writer.py
   • validate against AssetRecord schema
   • write {name, description, category, quantity, price} -> output/assets.json
```

`main.py` orchestrates the three stages.

## Project layout

```
Incub2/
├── PLAN.md
├── pyproject.toml / uv.lock          # uv-managed (Python 3.12)
├── docker-compose.yml                # pgvector/pgvector:pg16
├── .env.example
├── config/categories.yaml            # editable mock IT categories
├── src/
│   ├── models.py                     # Pydantic: RawLineItem, AssetRecord
│   ├── config.py                     # settings + categories loader
│   ├── llm.py                        # OpenAI + ChatOpenAI factories (local endpoint)
│   ├── stage1_extractor.py
│   ├── stage2_categorizer.py
│   ├── stage3_writer.py
│   └── store/{base.py, postgres_store.py}
├── scripts/seed_db.py
├── samples/                          # sample receipts
└── main.py                           # CLI: --receipt <path> --project-id <id> --out <path>
```

## Data model
- `RawLineItem{name, description="", quantity=1, price=0.0}` — Stage 1 output.
- `AssetRecord{name, description, category, quantity, price}` — Stage 3 output.

## Database (PostgreSQL + pgvector)
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE project_documents (
    id SERIAL PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT,
    content JSONB NOT NULL,      -- full purchase record / catalog
    embedding vector(384)        -- OPTIONAL; for shortlisting
);
CREATE INDEX ON project_documents (project_id);
```
- `DocumentStore` interface keeps the pipeline DB-agnostic. Key lookup = `WHERE project_id = %s`
  (no embeddings needed to run). Optional vector path uses `<=>` filtered by `project_id`, embeddings from
  local `sentence-transformers` (all-MiniLM-L6-v2, 384-dim).
- `scripts/seed_db.py` inserts mock projects (`PRJ-001`, `PRJ-002`) of IT purchase line items.

## Verification
1. `uv sync`; `sudo apt-get install -y poppler-utils`.
2. `docker-compose up -d`; `uv run scripts/seed_db.py`.
3. `curl http://192.168.100.1:5000/v1/models` to confirm the served model id.
4. Stage 1 alone on a sample image + PDF -> RawLineItem list.
5. `uv run main.py --receipt samples/receipt1.png --project-id PRJ-001 --out output/assets.json`.
6. Repeat with a PDF sample.
7. Edit `categories.yaml`, re-run, confirm the agent honors the change.

## Notes / risks
- Vision message shape and base path vary by OpenAI-compatible server — `/v1` confirmed on this server.
- Served model id is `gemma-4-31B-it` today (not Qwen); `LLM_MODEL` env overrides.
- deepagents + non-Claude model: pass a LangChain `ChatOpenAI` as `model=`; adapt if the installed version
  differs.
- Local model JSON discipline: tolerant parsing + a single re-prompt on parse/category-validation failure.
