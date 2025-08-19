import os
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from pgvector.sqlalchemy import Vector
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/webpagegenie")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))

engine = create_engine(DATABASE_URL, poolclass=NullPool, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(255), index=True, nullable=False)
    chunk_id = Column(Integer, index=True, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBED_DIM))
    # Optional DOM path for the chunk (e.g., html>body>div:nth-of-type(1)>p:nth-of-type(2))
    dom_path = Column(Text, nullable=True)


class RunLog(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    question = Column(Text, nullable=False)
    page_slug = Column(String(255), nullable=True)
    retrieval_method = Column(String(32), nullable=True)  # vector | hybrid
    retrieved_json = Column(Text, nullable=True)
    timings_json = Column(Text, nullable=True)
    answer_preview = Column(Text, nullable=True)
    saved = Column(Boolean, default=False, nullable=False)


def init_db() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
    Base.metadata.create_all(bind=engine)
    # Lightweight migrations and index creation
    with engine.begin() as conn:
        # Ensure embedding column dimension matches current EMBED_DIM
        try:
            conn.exec_driver_sql("DROP INDEX IF EXISTS documents_embedding_hnsw_idx;")
            conn.exec_driver_sql("DROP INDEX IF EXISTS documents_embedding_ivfflat_idx;")
            conn.exec_driver_sql(f"ALTER TABLE documents ALTER COLUMN embedding TYPE vector({EMBED_DIM});")
        except Exception:
            pass
        # Add dom_path column if missing
        conn.exec_driver_sql("ALTER TABLE IF EXISTS documents ADD COLUMN IF NOT EXISTS dom_path text;")
        # Add full-text search tsvector generated column and index
        conn.exec_driver_sql(
            "ALTER TABLE IF EXISTS documents ADD COLUMN IF NOT EXISTS content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;"
        )
        conn.exec_driver_sql(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind = 'i' AND c.relname = 'documents_content_tsv_idx'
                ) THEN
                    CREATE INDEX documents_content_tsv_idx ON documents USING GIN (content_tsv);
                END IF;
            END
            $$;
            """
        )
        # Create ANN index on embeddings. If dimension > 2000, fall back to IVFFLAT (HNSW limit)
        conn.exec_driver_sql(
            f"""
            DO $$
            DECLARE
                dim integer := {EMBED_DIM};
            BEGIN
                IF dim <= 2000 THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE c.relkind = 'i' AND c.relname = 'documents_embedding_hnsw_idx'
                    ) THEN
                        CREATE INDEX documents_embedding_hnsw_idx
                        ON documents
                        USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 200);
                    END IF;
                ELSE
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE c.relkind = 'i' AND c.relname = 'documents_embedding_ivfflat_idx'
                    ) THEN
                        CREATE INDEX documents_embedding_ivfflat_idx
                        ON documents
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100);
                    END IF;
                END IF;
            END
            $$;
            """
        )


def reset_db() -> None:
    """Drop and recreate all tables and indexes, preserving files on disk."""
    with engine.begin() as conn:
        try:
            conn.exec_driver_sql("DROP TABLE IF EXISTS runs CASCADE;")
            conn.exec_driver_sql("DROP TABLE IF EXISTS documents CASCADE;")
        except Exception:
            pass
    init_db()
