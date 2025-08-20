from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Tuple, Optional

from bs4 import BeautifulSoup
from openai import OpenAI

from .db import SessionLocal
from .vectors import upsert_chunks

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


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


def _extract_dom_chunks(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    # Extract text at block-level elements with simple DOM path metadata
    blocks = []
    block_tags = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "section",
        "article",
        "div",
    }
    def build_path(el) -> str:
        parts = []
        while el and el.name not in {"[document]"}:
            if not getattr(el, "name", None):
                break
            siblings_same = [s for s in el.parent.find_all(el.name, recursive=False)] if el.parent else []
            index = siblings_same.index(el) + 1 if el.parent and el in siblings_same else 1
            parts.append(f"{el.name}:nth-of-type({index})")
            el = el.parent
        return ">".join(reversed(parts))

    for tag in soup.find_all(block_tags):
        text = tag.get_text(separator=" ", strip=True)
        if not text:
            continue
        dom_path = build_path(tag)
        blocks.append((dom_path, text))
    return blocks


def ingest_single_page(path: Path) -> None:
    # Support both legacy pages/*.html and new pages/<slug>/index.html
    slug = path.parent.name if path.name == "index.html" and path.parent.name else path.stem
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    for script in soup(["script", "style"]):
        script.decompose()
    dom_chunks = _extract_dom_chunks(soup)
    # Fallback to flat text chunking if DOM extraction is sparse
    flat = None
    if len(dom_chunks) < 5:
        text = soup.get_text(separator="\n")
        parts = [p.strip() for p in text.splitlines() if p.strip()]
        flat = "\n".join(parts)
        dom_chunks = [(None, c) for c in _chunk_text(flat)]

    texts = [c for _, c in dom_chunks]
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model=EMBED_MODEL)
    vecs = embeddings.embed_documents(texts)
    with SessionLocal() as db:
        upsert_chunks(
            db,
            slug,
            [
                (i, text, vecs[i], dom_path if isinstance(dom_path, str) else None)
                for i, (dom_path, text) in enumerate(dom_chunks)
            ],
        )
    # Store full raw HTML as a special chunk for fallback retrieval when needed (no embedding)
    # We add it as chunk_id = -1 with dom_path = 'RAW_HTML'
    with SessionLocal() as db:
        upsert_chunks(
            db,
            f"{slug}__raw",
            [
                (-1, html, None, "RAW_HTML"),
            ],
        )


def ingest_all_pages(pages_dir: Path) -> None:
    # Legacy flat files
    for p in pages_dir.glob("*.html"):
        ingest_single_page(p)
    # New per-project directories with index.html
    for d in pages_dir.iterdir():
        if d.is_dir():
            idx = d / "index.html"
            if idx.exists():
                ingest_single_page(idx)
