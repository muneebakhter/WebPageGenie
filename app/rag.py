from __future__ import annotations

import os
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from openai import OpenAI
from langchain_core.documents import Document as LCDocument
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from .db import SessionLocal
from .vectors import similarity_search, hybrid_search_rrf
from .validate import validate_page_with_playwright

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
RERANK_MODEL = os.getenv("RERANK_MODEL", "rerank-english-v3.0")

# Disable Cohere reranking by default to avoid client incompatibilities
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
    validation: Dict[str, Any] | None = None
    validation_attempts: int = 0


def _retrieve(state: GraphState) -> GraphState:
    import time
    t0 = time.perf_counter()
    # Use OpenAI Python client directly to avoid proxy kwarg incompatibilities
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.embeddings.create(input=state.question, model=EMBED_MODEL)
    query_vec = list(resp.data[0].embedding)
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
        # If prior validation found client-side errors, ask to fix them specifically
        try:
            val = state.validation or {}
            errors = (val.get("console_errors") or []) + (val.get("page_errors") or [])
            if errors:
                parts.append("Known client errors to fix (from browser validation):\n")
                for err in errors[:10]:
                    parts.append(f"- {err}\n")
                parts.append("\n")
        except Exception:
            pass
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
    # Simple tool call: if the question starts with "image:" use the image tool and return the URL
    if state.question.strip().lower().startswith("image:"):
        try:
            prompt = state.question.split(":", 1)[1].strip()
            # Local HTTP call to our tool
            import requests
            payload = {"prompt": prompt, "page_slug": state.page_slug, "size": "1024x1024"}
            resp = requests.post(os.getenv("BASE_URL", "http://localhost:8000") + "/api/tools/image", json=payload, timeout=180)
            data = resp.json() if resp.ok else {"error": resp.text}
            url = data.get("url") or data.get("static_url") or "(no url)"
            state.answer = f"Image generated: {url}"
            return state
        except Exception as e:
            state.answer = f"Image tool error: {e}"
            return state

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
    # Reranking disabled; return as-is
    return docs


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("generate", _generate)
    # Optional: validation node to check DOM/console after generation
    def _validate(state: GraphState) -> GraphState:
        try:
            if not state.page_slug:
                return state
            import os
            base_url = os.getenv("BASE_URL", "http://localhost:8000")
            url = f"{base_url}/pages/{state.page_slug}/index.html"
            result = validate_page_with_playwright(url)
            state.validation = result  # type: ignore
            state.validation_attempts = int(getattr(state, "validation_attempts", 0) or 0) + 1
            return state
        except Exception:
            return state

    def _needs_fix(state: GraphState) -> str:
        v = getattr(state, "validation", None) or {}
        errors = (v.get("console_errors") or []) + (v.get("page_errors") or [])
        # If there are errors and we have not exceeded attempts, go back to generate for another pass
        attempts = int(getattr(state, "validation_attempts", 0) or 0)
        MAX_ATTEMPTS = 2
        if errors and attempts <= MAX_ATTEMPTS:
            return "generate"
        return END

    graph.add_node("validate", _validate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", _needs_fix, {"generate": "generate", END: END})
    return graph.compile()
