from __future__ import annotations

from typing import Iterable, List, Tuple
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from pgvector.sqlalchemy import cosine_distance

from .db import Document


def upsert_chunks(db: Session, slug: str, chunks: Iterable[Tuple[int, str, list[float]]]) -> None:
    db.execute(delete(Document).where(Document.slug == slug))
    for chunk_id, content, embedding in chunks:
        db.add(Document(slug=slug, chunk_id=chunk_id, content=content, embedding=embedding))
    db.commit()


def similarity_search(db: Session, query_embedding: list[float], slug: str | None = None, k: int = 5) -> List[Document]:
    stmt = select(Document)
    if slug:
        stmt = stmt.where(Document.slug == slug)
    stmt = stmt.order_by(cosine_distance(Document.embedding, query_embedding)).limit(k)
    return list(db.execute(stmt).scalars().all())
