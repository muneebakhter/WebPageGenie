import os
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from pgvector.sqlalchemy import Vector

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/webpagegenie")
EMBED_DIM = int(os.getenv("EMBED_DIM", "3072"))

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


def init_db() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
    Base.metadata.create_all(bind=engine)
    # Lightweight migrations and index creation
    with engine.begin() as conn:
        # Add dom_path column if missing
        conn.exec_driver_sql("ALTER TABLE IF EXISTS documents ADD COLUMN IF NOT EXISTS dom_path text;")
        # Create HNSW index for fast ANN search on cosine distance (requires pgvector >= 0.6.0)
        conn.exec_driver_sql(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind = 'i' AND c.relname = 'documents_embedding_hnsw_idx'
                ) THEN
                    CREATE INDEX documents_embedding_hnsw_idx
                    ON documents
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 200);
                END IF;
            END
            $$;
            """
        )
