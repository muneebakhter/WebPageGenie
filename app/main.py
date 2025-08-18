from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import List

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .db import init_db
from .rag import build_graph, GraphState
from .ws import ReloadWebSocketManager
from .ingest import ingest_all_pages, ingest_single_page

BASE_DIR = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE_DIR / "pages"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

app = FastAPI(title="WebPageGenie")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ws_manager = ReloadWebSocketManager()
_langgraph_app = build_graph()


class ChatRequest(BaseModel):
    message: str
    page_slug: str | None = None


@app.on_event("startup")
async def on_startup() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    try:
        ingest_all_pages(PAGES_DIR)
    except Exception:
        pass
    loop = asyncio.get_event_loop()
    loop.create_task(_watch_pages_task())


async def _watch_pages_task() -> None:
    try:
        from watchfiles import awatch
    except Exception:
        return
    async for _changes in awatch(PAGES_DIR):
        try:
            ingest_all_pages(PAGES_DIR)
        except Exception:
            pass
        await ws_manager.broadcast_reload()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    pages = sorted([p.stem for p in PAGES_DIR.glob("*.html")])
    return templates.TemplateResponse("index.html", {"request": request, "pages": pages})


@app.get("/page", response_class=HTMLResponse)
async def get_page(id: str):
    path = PAGES_DIR / f"{id}.html"
    if not path.exists():
        return HTMLResponse(status_code=404, content=f"<h1>404</h1><p>Page '{id}' not found.</p>")
    content = path.read_text(encoding="utf-8")
    reload_snippet = (
        "<script>(function(){try{var proto=location.protocol==='https:'?'wss':'ws';var ws=new WebSocket(proto+'://'+location.host+'/ws');ws.onmessage=function(e){if(e.data==='reload'){location.reload();}}}catch(e){}})();</script>"
    )
    content_out = content + "\n" + reload_snippet
    return HTMLResponse(content=content_out)


@app.get("/graph", response_class=HTMLResponse)
async def graph_view(request: Request):
    try:
        mermaid = _langgraph_app.get_graph().draw_mermaid()
    except Exception:
        mermaid = "graph TD; A[retrieve] --> B[generate]; B --> C[END];"
    return templates.TemplateResponse("graph.html", {"request": request, "mermaid": mermaid})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    state = GraphState(question=req.message, page_slug=req.page_slug)
    result = _langgraph_app.invoke(state)
    answer = result["answer"] if isinstance(result, dict) else result.answer

    saved = False
    if "<html" in (answer or "") and req.page_slug:
        out_path = PAGES_DIR / f"{req.page_slug}.html"
        out_path.write_text(answer, encoding="utf-8")
        try:
            ingest_single_page(out_path)
        except Exception:
            pass
        await ws_manager.broadcast_reload()
        saved = True

    return {"saved": saved, "answer": answer}
