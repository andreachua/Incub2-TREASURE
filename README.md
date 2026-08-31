# Asset Management Extraction Pipeline

Turns a receipt (image or PDF) + a `project_id` into a structured asset register:
`name`, `description`, `category`, `quantity`, `price` per line item.

3 stages:
1. **Extract** — a local OpenAI-compatible **VLM** reads the receipt and returns raw line items.
2. **Categorize** — a **deepagents** agent (same local model) fetches the project's document from
   **Postgres/pgvector** by `project_id`, matches the most related line item, enriches the description,
   and assigns a category from an editable list.
3. **Write** — validated records are written to a JSON file.

See `PLAN.md` for the full design.

## Prerequisites
- [`uv`](https://docs.astral.sh/uv/), Docker, and `poppler-utils` (for PDF input: `sudo apt-get install -y poppler-utils`).
- A local OpenAI-compatible endpoint (default `http://192.168.100.1:5000/v1`).

## Setup
```bash
uv sync                                    # install deps (Python 3.12, managed by uv)
cp .env.example .env                        # then edit LLM_MODEL / endpoint / DB as needed

# Start Postgres + pgvector.
# NOTE: the system's docker-compose v1.29.2 is incompatible with this Docker
# ("http+docker" bug), so start the DB directly:
docker run -d --name assets-pgvector \
  -e POSTGRES_DB=assets -e POSTGRES_USER=assets -e POSTGRES_PASSWORD=assets \
  -p 5432:5432 -v assets_pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16
# (docker-compose.yml is provided for environments with Compose v2: `docker compose up -d`.)

uv run scripts/seed_db.py --no-embed        # seed mock projects PRJ-001 / PRJ-002
# or: uv run scripts/seed_db.py             # also populate embeddings (enables the vector shortlist)
```

## Configure the model
The model id must match what the endpoint serves (`GET /v1/models`). Set it in `.env`:
```
LLM_MODEL=gemma-4-31B-it      # change to Qwen3.8-27B (etc.) when that model is loaded
LLM_BASE_URL=http://192.168.100.1:5000/v1
```

## Run
```bash
uv run main.py --receipt samples/receipt1.png --project-id PRJ-001 --out output/assets.json
uv run main.py --receipt samples/po2.pdf      --project-id PRJ-002 --out output/assets_prj002.json
```

## Categories
Edit `config/categories.yaml` (name + description). The agent is constrained to those names — changes take
effect on the next run, no code change needed.

## Layout
```
main.py                     CLI orchestrator
config/categories.yaml      editable IT categories
src/stage1_extractor.py     VLM extraction (image/PDF)
src/stage2_categorizer.py   deepagents agent + tools (+ deterministic fallback)
src/stage3_writer.py        JSON output
src/store/                  DocumentStore interface + Postgres/pgvector impl
scripts/seed_db.py          mock project documents
```
