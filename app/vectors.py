from __future__ import annotations

from typing import Iterable, List, Tuple, Optional, Dict
from sqlalchemy import select, delete, text, bindparam
from sqlalchemy.orm import Session

from pgvector.sqlalchemy import Vector

from .db import Document, EMBED_DIM


def upsert_chunks(
    db: Session,
    slug: str,
    chunks: Iterable[Tuple[int, str, Optional[list[float]], Optional[str]]],
) -> None:
    db.execute(delete(Document).where(Document.slug == slug))
    for chunk_id, content, embedding, dom_path in chunks:
        db.add(
            Document(
                slug=slug,
                chunk_id=chunk_id,
                content=content,
                embedding=embedding,
                dom_path=dom_path,
            )
        )
    db.commit()


def similarity_search(db: Session, query_embedding: list[float], slug: str | None = None, k: int = 5) -> List[Document]:
    # Optional tuning for HNSW ef_search
    try:
        import os
        ef = int(os.getenv("HNSW_EF_SEARCH", "100"))
        db.execute(text("SET LOCAL hnsw.ef_search = :ef").bindparams(ef=ef))
    except Exception:
        # If the parameter isn't supported, the transaction enters a failed state.
        # Roll it back so subsequent queries succeed.
        try:
            db.rollback()
        except Exception:
            pass
    # Optional tuning for IVFFLAT probes
    try:
        import os
        probes = int(os.getenv("IVFFLAT_PROBES", "10"))
        db.execute(text("SET LOCAL ivfflat.probes = :p").bindparams(p=probes))
    except Exception:
        # Roll back on failure to clear aborted transaction state
        try:
            db.rollback()
        except Exception:
            pass

    stmt = select(Document)
    if slug:
        stmt = stmt.where(Document.slug == slug)
    # Order by cosine distance using pgvector comparator to ensure correct typing
    qp = bindparam("query_embedding", value=query_embedding, type_=Vector(EMBED_DIM))
    stmt = stmt.order_by(Document.embedding.cosine_distance(qp)).limit(k)
    return list(db.execute(stmt).scalars().all())


def lexical_search(db: Session, query: str, slug: str | None = None, k: int = 20) -> List[Document]:
    # Use PostgreSQL full-text search on generated tsvector
    # Rank using ts_rank, filter by slug optional
    where_clause = "content_tsv @@ plainto_tsquery('english', :q)"
    params: Dict[str, object] = {"q": query}
    if slug:
        where_clause += " AND slug = :slug"
        params["slug"] = slug
    stmt = (
        select(Document)
        .where(text(where_clause))
        .order_by(text("ts_rank(content_tsv, plainto_tsquery('english', :q)) DESC"))
        .limit(k)
        .params(**params)
    )
    return list(db.execute(stmt).scalars().all())


def hybrid_search_rrf(
    db: Session,
    query: str,
    query_embedding: list[float],
    slug: str | None = None,
    k_vector: int = 20,
    k_lexical: int = 20,
    k_final: int = 5,
    rrf_k: int = 60,
) -> List[Document]:
    # Reciprocal Rank Fusion of lexical and vector results
    vec_results = similarity_search(db, query_embedding, slug=slug, k=k_vector)
    lex_results = lexical_search(db, query, slug=slug, k=k_lexical)
    scores: Dict[int, float] = {}
    order: Dict[int, Document] = {}
    for rank, doc in enumerate(vec_results):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1.0 / (rrf_k + rank + 1)
        order.setdefault(doc.id, doc)
    for rank, doc in enumerate(lex_results):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1.0 / (rrf_k + rank + 1)
        order.setdefault(doc.id, doc)
    ranked = sorted(order.values(), key=lambda d: scores.get(d.id, 0.0), reverse=True)
    return ranked[:k_final]
