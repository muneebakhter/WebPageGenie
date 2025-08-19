from __future__ import annotations

from typing import Iterable, List, Tuple, Optional
from sqlalchemy import select, delete, text
from sqlalchemy.orm import Session

from .db import Document


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
        pass

    stmt = select(Document)
    if slug:
        stmt = stmt.where(Document.slug == slug)
    # Use the <=> operator for cosine distance in pgvector
    stmt = stmt.order_by(text("embedding <=> :query_embedding")).params(query_embedding=query_embedding).limit(k)
    return list(db.execute(stmt).scalars().all())
