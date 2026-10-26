"""
rag.py - Vector store and permission-aware retrieval.

Access control is applied INSIDE the vector database query (a ChromaDB
`where` filter). Unauthorized chunks never become similarity-search
candidates, so they cannot be ranked, returned, or leaked into a prompt.
The alternative, "retrieve top-k and then filter in Python", is unsafe: it
leaks through ranking and quietly returns fewer results to users with less
access.

Each chunk carries scalar metadata (ChromaDB metadata values cannot be lists):
    {"tenant_id": str, "required_role": str, ...}
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from auth import User

logger = logging.getLogger("rag.store")

EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
COLLECTION_NAME = "documents"
DEFAULT_TOP_K = 4
# Chunks that pass the access filter must also clear this cosine-similarity
# bar. It stops unrelated but authorized chunks from being passed to the LLM
# as "context".
MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.30"))
# all-MiniLM-L6-v2 truncates input at 256 word-pieces, so chunks stay well below that.
CHUNK_WORDS = 120
CHUNK_OVERLAP = 20


@dataclass(frozen=True, slots=True)
class IngestResult:
    doc_id: str
    chunk_count: int


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    similarity: float
    source: str
    tenant_id: str
    required_role: str


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    logger.info("Loading embedding model %s", EMBEDDING_MODEL)
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _get_collection() -> chromadb.Collection:
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    # embedding_function=None: every vector comes from our own model, so Chroma
    # never silently downloads or uses a different default embedder.
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
    )


def warm_up() -> None:
    """Load the model and create the collection before serving traffic.

    Also runs one embedding call, so the first real request is not slowed by lazy setup.
    """
    _get_collection()
    _embed(["warm-up"])


def _embed(texts: list[str]) -> list[list[float]]:
    vectors = _get_model().encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return vectors.tolist()


def _chunk(text: str) -> list[str]:
    words = text.split()
    if len(words) <= CHUNK_WORDS:
        return [" ".join(words)] if words else []
    step = CHUNK_WORDS - CHUNK_OVERLAP
    return [" ".join(words[i : i + CHUNK_WORDS]) for i in range(0, len(words) - CHUNK_OVERLAP, step)]


def _require_scalar(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {type(value).__name__}")
    return value.strip()


def add_document(
    text: str,
    *,
    tenant_id: str,
    required_role: str,
    source: str = "untitled",
    ingested_by: str = "system",
) -> IngestResult:
    """Chunk, embed, and upsert a document with strict access metadata.

    The caller (the API layer) is responsible for deciding WHETHER the
    principal may write to (tenant_id, required_role). This function
    guarantees HOW it is stored: every chunk gets exactly those scalar values.

    IDs come from a hash of (tenant, role, content). Re-ingesting the same
    document therefore overwrites it instead of adding a duplicate. Because the
    tenant is part of the hash, two tenants that upload identical text can
    never overwrite each other's records.
    """
    tenant_id = _require_scalar("tenant_id", tenant_id)
    required_role = _require_scalar("required_role", required_role)

    chunks = _chunk(text)
    if not chunks:
        raise ValueError("Document contains no text")

    fingerprint = "\x1f".join((tenant_id, required_role, " ".join(chunks)))
    doc_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]

    ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "tenant_id": tenant_id,
            "required_role": required_role,
            "doc_id": doc_id,
            "chunk_index": i,
            "source": source,
            "ingested_by": ingested_by,
        }
        for i in range(len(chunks))
    ]

    _get_collection().upsert(ids=ids, documents=chunks, embeddings=_embed(chunks), metadatas=metadatas)
    logger.info(
        "ingest.ok doc_id=%s chunks=%d tenant=%s role=%s by=%s",
        doc_id, len(chunks), tenant_id, required_role, ingested_by,
    )
    return IngestResult(doc_id=doc_id, chunk_count=len(chunks))


def build_access_filter(user: User) -> dict[str, Any]:
    """Return the ChromaDB `where` clause for this user's access scope.

    Fails closed: if the principal is missing a tenant or role, the function
    raises an error. It never falls back to an unfiltered query.
    """
    tenant_id = _require_scalar("user.tenant_id", user.tenant_id)
    role = _require_scalar("user.role", user.role.value)
    return {
        "$and": [
            {"tenant_id": {"$eq": tenant_id}},
            {"required_role": {"$eq": role}},
        ]
    }


def secure_query(
    query: str,
    user: User,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[RetrievedChunk]:
    """Similarity search restricted to the user's (tenant_id, role) scope."""
    where = build_access_filter(user)
    result = _get_collection().query(
        query_embeddings=_embed([query]),
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids = result["ids"][0] if result["ids"] else []
    docs = result["documents"][0] if result["documents"] else []
    metas = result["metadatas"][0] if result["metadatas"] else []
    dists = result["distances"][0] if result["distances"] else []

    chunks: list[RetrievedChunk] = []
    for chunk_id, text, meta, distance in zip(ids, docs, metas, dists):
        # Defense in depth: re-check every result against the principal. This
        # should never trigger. If it does, the store or filter is broken and
        # the chunk must not leave this function.
        if meta.get("tenant_id") != user.tenant_id or meta.get("required_role") != user.role.value:
            logger.error("SECURITY access_filter_violation chunk=%s user=%s", chunk_id, user.user_id)
            continue

        similarity = 1.0 - float(distance)
        if similarity < min_similarity:
            continue

        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=text,
                similarity=round(similarity, 4),
                source=str(meta.get("source", "untitled")),
                tenant_id=str(meta["tenant_id"]),
                required_role=str(meta["required_role"]),
            )
        )

    logger.info(
        "query.ok user=%s tenant=%s role=%s candidates=%d returned=%d",
        user.user_id, user.tenant_id, user.role.value, len(ids), len(chunks),
    )
    return chunks
