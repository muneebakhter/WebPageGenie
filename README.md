## WebPageGenie

RAG-powered AI chatbot that generates and edits single-file HTML5/CSS3/JS webpages. Uses FastAPI, LangGraph/LangChain, OpenAI, and pgvector. Hot-reloads edited pages via WebSockets. Shows the LangGraph flow in a small GUI. Pages are served by FastAPI using a `?id=slug` querystring.

### Features
- RAG over vectorized webpages stored in PostgreSQL with pgvector
- Chat endpoint invokes a LangGraph pipeline (retrieve -> generate)
- If the model returns full HTML and a `page_slug` is set, the HTML is saved to `pages/<slug>.html`, ingested into the vector store, and clients hot-reload
- Simple graph UI page rendering Mermaid from the compiled graph
- Sample `home` and `about` pages with cross-links
- Selenium smoke test
- Hybrid retrieval (BM25 full-text + vector) with Reciprocal Rank Fusion
- Optional reranking via Cohere
- Observability: per-request timings, retrieval method selection, and run logs API

### Requirements
- Python 3.11+
- Docker (for Postgres + pgvector)
- OpenAI API key

### Setup
1. Clone this repo and `cd WebPageGenie`.
2. Create a `.env` file with at least:
   ```bash
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-5
   EMBED_MODEL=text-embedding-3-small
   EMBED_DIM=1536
   # Optional reranking with Cohere
   COHERE_API_KEY=
   RERANK_MODEL=rerank-english-v3.0
   # Optional pgvector HNSW search tuning
   HNSW_EF_SEARCH=100
   # Optional pgvector IVFFLAT tuning (used automatically if EMBED_DIM > 2000)
   IVFFLAT_PROBES=10
   DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/webpagegenie
   HOST=0.0.0.0
   PORT=8000
   BASE_URL=http://localhost:8000
   ```
3. Install deps:
   ```bash
   python -m venv .venv && . .venv/bin/activate  # Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
4. Start Postgres with pgvector:
   ```bash
   docker compose up -d
   ```
5. Initialize DB and ingest sample pages:
   ```bash
   python scripts/ingest_pages.py
   ```
6. Run the app:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
7. Open `http://localhost:8000`.

### Usage
- Use the chat box on `/` to ask for changes. Set `page_slug` to `home` or `about` to edit those pages, or any new slug to create a new page.
- When an edit saves, connected browsers reload automatically.
- View the graph at `/graph`.
 - Choose retrieval mode (Vector or Hybrid) from the dropdown before sending a query.

### Data model
- Table `documents(slug, chunk_id, content, embedding, dom_path)` with pgvector column.
- Table `runs` for observability: records question, retrieval method, retrieved chunks, timings, answer preview, and whether a page was saved.
- Retrieval uses cosine distance to find top-K chunks. Generation uses `gpt-5` for instructions and page edits.

### Selenium smoke test
- Ensure the app is running at `http://localhost:8000`, then:
  ```bash
  pytest tests/test_selenium_smoke.py
  ```

### Manual testing
1. Start Postgres and the app:
   ```bash
   docker compose up -d
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
2. Open `http://localhost:8000`.
3. Try a question with retrieval set to Vector; observe timings in the UI.
4. Switch retrieval to Hybrid and ask the same question; compare results and timings.
5. If `COHERE_API_KEY` is set, confirm reranking runs by checking `/api/runs` for `timings.rerank_ms` > 0.

### Notes
- The ingestion pipeline strips `script`/`style` and performs DOM-aware chunking with `dom_path` metadata; falls back to flat chunking when needed.
- A raw HTML snapshot is stored under a separate slug `slug__raw` for potential fallback usage.
- If the model response contains `<html` and `page_slug` is provided, the HTML gets saved and re-ingested.
- Postgres full-text search (GIN on `content_tsv`) powers lexical retrieval; pgvector HNSW powers ANN vector search.
 - If your embedding dimension exceeds 2000 (e.g., 3072 for `text-embedding-3-large`), the app automatically creates an IVFFLAT index instead of HNSW. Tune with `IVFFLAT_PROBES`.

### Hybrid retrieval & reranking
- Use the retrieval selector on the home page to choose between:
  - Vector: pgvector cosine similarity
  - Hybrid: Reciprocal Rank Fusion of BM25-style full-text (`plainto_tsquery`) and vector results
- If `COHERE_API_KEY` is set, an additional reranking step orders retrieved chunks using Cohere's cross-encoder model.

### Observability
- Each chat request logs a run in the `runs` table with timings for embed/retrieve/rerank and a preview of retrieved chunks.
- Inspect recent runs: open `http://localhost:8000/api/runs`.
- The UI shows retrieval method and timings appended beneath the answer.

### Next steps / Improvements
- Vector stores:
  - Add FAISS and HNSW indexes (e.g., `faiss-cpu`, `hnswlib`) for in-memory/local dev with fast ANN
  - Add `whoosh` or `lunr` for hybrid lexical + vector search
  - Explore pgvector HNSW index (`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`) and tune `ef_search`
- Chunking & parsing:
  - Use language-aware HTML chunkers, preserve DOM paths in metadata
  - Store entire raw HTML as an additional document for fallbacks
- RAG quality:
  - Add reranking (e.g., `cohere-rerank` or local cross-encoder)
  - Add query rewriting and guardrails
- Editing strategies:
  - Focused diff-based edits: extract target section and ask model to update that fragment only
  - Validate resulting HTML with a linter and auto-fix common issues
- Observability:
  - Add a small run log UI; store requests/responses for audit
  - Capture tokens/latency and show in the GUI
- Browser live UX:
  - Add an in-page overlay panel to preview model diffs before saving
  - Add manual reload and toast notifications
- Agents/Tools:
  - Add tools for reading/writing the filesystem in a sandbox
  - Add a crawl tool to pull in other webpages for context
- Security:
  - Sanitize content and enforce a safe allowlist for script tags
  - Auth for admin edit capabilities

### Troubleshooting
- If pgvector extension errors: ensure container image is `pgvector/pgvector` and DB is healthy
- If embeddings fail: verify `OPENAI_API_KEY` and models are enabled
- Windows WSL: prefer launching with `uvicorn` from WSL for consistent paths
