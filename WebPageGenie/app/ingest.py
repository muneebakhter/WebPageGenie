from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Tuple

from bs4 import BeautifulSoup
from langchain_openai import OpenAIEmbeddings

from .db import SessionLocal
from .vectors import upsert_chunks

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def _chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
        if end == len(text):
            break
    return chunks


def ingest_single_page(path: Path) -> None:
    slug = path.stem
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text(separator="\n")
    parts = [p.strip() for p in text.splitlines() if p.strip()]
    flat = "\n".join(parts)

    chunks = _chunk_text(flat)
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model=EMBED_MODEL)
    vecs = embeddings.embed_documents(chunks)
    with SessionLocal() as db:
        upsert_chunks(db, slug, [(i, chunk, vecs[i]) for i, chunk in enumerate(chunks)])


def ingest_all_pages(pages_dir: Path) -> None:
    pages = list(pages_dir.glob("*.html"))
    for p in pages:
        ingest_single_page(p)
