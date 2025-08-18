from __future__ import annotations

import os
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.schema import Document as LCDocument
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from .db import SessionLocal
from .vectors import similarity_search

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")


class GraphState(BaseModel):
    question: str
    page_slug: str | None = None
    retrieved: List[LCDocument] = Field(default_factory=list)
    answer: str | None = None


def _retrieve(state: GraphState) -> GraphState:
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model=EMBED_MODEL)
    query_vec = embeddings.embed_query(state.question)
    with SessionLocal() as db:
        docs = similarity_search(db, query_vec, slug=state.page_slug, k=5)
    lcdocs = [LCDocument(page_content=d.content, metadata={"slug": d.slug, "chunk_id": d.chunk_id}) for d in docs]
    state.retrieved = lcdocs
    return state


def _generate(state: GraphState) -> GraphState:
    llm = ChatOpenAI(api_key=OPENAI_API_KEY, model=OPENAI_MODEL, temperature=0.2)
    context = "\n\n".join(d.page_content for d in state.retrieved)
    system = (
        "You are WebPageGenie, an assistant that edits or creates single-file HTML5/CSS3/JS webpages. "
        "Use the provided context and user instructions to produce either: "
        "1) a clear natural-language answer, or 2) a full updated HTML document when asked to make page edits."
    )
    user = f"Question: {state.question}\n\nContext:\n{context}"
    msg = llm.invoke([
        ("system", system),
        ("user", user)
    ])
    state.answer = msg.content
    return state


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("generate", _generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
