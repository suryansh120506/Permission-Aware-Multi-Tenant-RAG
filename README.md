# Permission-Aware Multi-Tenant RAG API

A retrieval-augmented generation service that enforces **tenant and role isolation inside the vector database query**. A user can never retrieve, rank against, or leak into a prompt any chunk they are not authorized to see.

It runs fully locally: `sentence-transformers` for embeddings, an in-memory ChromaDB store, and a mocked LLM. No API keys are needed.

```
┌──────────────┐  HTTP + Bearer token  ┌──────────────┐  where: tenant_id AND role  ┌──────────┐
│ Streamlit UI │ ────────────────────▶ │ FastAPI      │ ──────────────────────────▶ │ ChromaDB │
│  (app.py)    │                       │  (api.py)    │                             │ (rag.py) │
└──────────────┘                       └──────┬───────┘                             └──────────┘
                                              │ token → User(user_id, tenant_id, role)
                                              ▼
                                        auth.py (mock IdP)
```

| File | Responsibility |
|---|---|
| `auth.py` | Resolves `Authorization: Bearer <token>` into an immutable `User(user_id, tenant_id, role)`. Tokens are stored as SHA-256 digests and compared in constant time. |
| `rag.py` | Chunking, embedding (`all-MiniLM-L6-v2`), `add_document`, and `secure_query`. The `$and` `where` filter runs inside ChromaDB. |
| `api.py` | `POST /v1/ingest` and `POST /v1/query`, write authorization, the mock LLM (1 s delay), and audit logging. |
| `app.py` | Streamlit dashboard. It talks to the API **only over HTTP** (`requests`) and never imports `rag` or `auth`. |
| `.streamlit/config.toml` | Dark theme for native Streamlit widgets. The portal styling itself is custom CSS in `app.py`. |
| `test_isolation.py` | Security regression tests (cross-role, cross-tenant, auth, write authorization). |

## Security model

- **Identity comes from the token only.** On both endpoints, `tenant_id` and `role` come from the authenticated principal. Metadata in the request body is only a claim that the server checks.
- **Pre-retrieval filtering.** `secure_query` passes `{"$and": [{"tenant_id": {"$eq": …}}, {"required_role": {"$eq": …}}]}` to ChromaDB. Unauthorized chunks are never similarity candidates. That is the difference from post-filtering, which leaks through rankings and top-k starvation.
- **Fails closed.** A principal with an empty tenant or role raises an error. There is no code path that runs an unfiltered query.
- **Defense in depth.** Every returned chunk's metadata is re-checked against the principal, and any mismatch is logged as a `SECURITY` event and dropped.
- **Write authorization.** Users can only ingest into their own tenant **and** role scope, so Engineering cannot plant documents or prompt injections into HR's retrieval set.
- **Collision-safe IDs.** Chunk IDs hash `(tenant, role, content)`. Re-ingesting the same document is idempotent, and two tenants uploading identical text can never overwrite each other.
- **Relevance gate.** Authorized chunks below `RAG_MIN_SIMILARITY` (default `0.30`) are discarded. If nothing remains, the LLM is not called and the API returns `No context found`.
- **Strict schemas.** Pydantic models use `extra="forbid"` and length limits, and `required_role` is an enum. List-valued or unknown fields get a 422.

## Run it

Requires Python 3.10–3.12 (check that PyTorch and ChromaDB wheels exist before using newer versions).

```powershell
# 1. Install (one time)
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Terminal 1: API (first start downloads the ~90 MB embedding model)
uvicorn api:app --host 127.0.0.1 --port 8000

# 3. Terminal 2: dashboard (activate the venv first)
streamlit run app.py
```

Or start both from one PowerShell window:

```powershell
Start-Process -NoNewWindow .venv\Scripts\uvicorn.exe "api:app --host 127.0.0.1 --port 8000"
.venv\Scripts\streamlit.exe run app.py
```

On macOS/Linux:

```bash
uvicorn api:app --port 8000 & streamlit run app.py
```

The dashboard waits for `/healthz`, then ingests the two demo documents through `/v1/ingest`: the HR doc as Alice and the Engineering doc as Bob. Open http://localhost:8501 and:

1. As **Bob (Engineering)**, ask *"What is the bonus pool?"* and you get **No context found**.
2. Switch to **Alice (HR)** and ask the same question: *"The 2026 Q3 Bonus pool is $500,000."*

Interactive API docs: http://127.0.0.1:8000/docs.

### curl

```bash
curl -s localhost:8000/v1/query -H "Authorization: Bearer bob-eng-token" \
     -H "Content-Type: application/json" -d '{"question":"What is the bonus pool?"}'
```

### Tests

```bash
pytest -q
```

## Configuration

| Env var | Default | Used by |
|---|---|---|
| `RAG_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | API |
| `RAG_MIN_SIMILARITY` | `0.30` | API |
| `RAG_API_URL` | `http://127.0.0.1:8000` | Dashboard |

## Production roadmap

- Replace `auth.py` with OIDC/JWT verification and map claims to `User`.
- Use a persistent vector store (Chroma server, pgvector with row-level security, or Qdrant with payload filters).
- Support multi-role users (see the note below), rate limiting per principal, and structured audit logs sent to a SIEM.
- Swap `MockLLM` for a real client. `build_prompt` already delimits retrieved text as untrusted data.

> **Multi-role users.** ChromaDB metadata can't hold lists, so each chunk has a single `required_role`. A user with several roles can be supported with `{"required_role": {"$in": user.roles}}` in the same filter, without changing how chunks are stored.
