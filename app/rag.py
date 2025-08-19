from __future__ import annotations

import os
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.documents import Document as LCDocument
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from .db import SessionLocal
from .vectors import similarity_search, hybrid_search_rrf

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
RERANK_MODEL = os.getenv("RERANK_MODEL", "rerank-english-v3.0")

_cohere_client = None
if COHERE_API_KEY:
    try:
        import cohere  # type: ignore

        _cohere_client = cohere.Client(api_key=COHERE_API_KEY)
    except Exception:
        _cohere_client = None


class GraphState(BaseModel):
    question: str
    page_slug: str | None = None
    retrieved: List[LCDocument] = Field(default_factory=list)
    answer: str | None = None
    retrieval_method: str = "vector"  # vector | hybrid
    timings: Dict[str, float] = Field(default_factory=dict)
    selected_html: str | None = None
    selected_path: List[str] = Field(default_factory=list)
    system_context: str | None = None


def _retrieve(state: GraphState) -> GraphState:
    import time
    t0 = time.perf_counter()
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model=EMBED_MODEL)
    query_vec = embeddings.embed_query(state.question)
    t1 = time.perf_counter()
    with SessionLocal() as db:
        if state.retrieval_method == "hybrid":
            docs = hybrid_search_rrf(db, state.question, query_vec, slug=state.page_slug, k_final=5)
        else:
            docs = similarity_search(db, query_vec, slug=state.page_slug, k=5)
    t2 = time.perf_counter()
    lcdocs = [
        LCDocument(
            page_content=d.content,
            metadata={"slug": d.slug, "chunk_id": d.chunk_id, "dom_path": getattr(d, "dom_path", None)},
        )
        for d in docs
    ]
    # Optional reranking
    t_rerank0 = time.perf_counter()
    state.retrieved = _rerank_docs(state.question, lcdocs)
    t3 = time.perf_counter()
    state.timings = {
        "embed_ms": (t1 - t0) * 1000.0,
        "retrieve_ms": (t2 - t1) * 1000.0,
        "rerank_ms": (t3 - t_rerank0) * 1000.0,
    }
    return state


def _generate(state: GraphState) -> GraphState:
    import time
    t0 = time.perf_counter()
    llm = ChatOpenAI(api_key=OPENAI_API_KEY, model=OPENAI_MODEL, temperature=0.15)
    context = "\n\n".join(d.page_content for d in state.retrieved)
    # Edit-focused guidance: when a page_slug is set and the context likely contains the current HTML,
    # request minimal diffs and preserve structure/assets.
    default_context = (
        "You are an expert frontend developer familiar with the latest frontend JS frameworks and tasked as a contractor to create SPAs with enterprise-grade professional designs. "
        "Make modern-looking pages with tasteful graphics, subtle animations, and modals where appropriate. Here is your task from the client:"
    )
    system = (
        (state.system_context or default_context) + "\n\n"
        "You are WebPageGenie, an assistant that edits or creates single-file HTML5/CSS3/JS webpages. "
        "Prefer small, targeted edits to the existing page when possible. Preserve existing structure, styles, and links. "
        "Only replace or add the minimal necessary sections. If a full page is necessary, ensure it remains compatible with existing assets."
    )
    selected_block = (state.selected_html or "").strip()
    if state.page_slug:
        parts: List[str] = []
        parts.append(f"Task: {state.question}\n\n")
        parts.append("Current page content (may be partial):\n")
        parts.append(f"{context}\n\n")
        if selected_block:
            parts.append("Selected element (focus your edits here):\n")
            parts.append(selected_block)
            parts.append("\n\n")
        parts.append(
            "Instructions:\n"
            "- Make minimal edits to satisfy the task.\n"
            "- Keep existing classes/IDs and asset references (images, CSS, JS) intact when possible.\n"
            "- If you must add CSS/JS, inline small bits; otherwise reference relative files under ./ .\n"
            "- Return a complete, valid HTML document."
        )
        user = "".join(parts)
    else:
        user = f"Task: {state.question}\n\nContext:\n{context}"
    msg = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])
    t1 = time.perf_counter()
    state.answer = msg.content
    # track generation time
    state.timings = {**(state.timings or {}), "generate_ms": (t1 - t0) * 1000.0}
    return state


def _rerank_docs(query: str, docs: List[LCDocument]) -> List[LCDocument]:
    if not docs:
        return docs
    if _cohere_client is None:
        return docs
    try:
        doc_texts = [d.page_content for d in docs]
        result = _cohere_client.rerank(
            model=RERANK_MODEL,
            query=query,
            documents=doc_texts,
            top_n=len(doc_texts),
        )
        # Cohere returns items with index and relevance score
        ordered = sorted(result, key=lambda r: r.relevance_score, reverse=True)
        return [docs[r.index] for r in ordered]
    except Exception:
        return docs


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("generate", _generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
