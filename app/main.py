from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import List

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .db import init_db, SessionLocal, RunLog
from .rag import build_graph, GraphState
from .rag import _retrieve as rag_retrieve  # type: ignore
from .rag import _generate as rag_generate  # type: ignore
from .ws import ReloadWebSocketManager
from .ingest import ingest_all_pages, ingest_single_page
from datetime import datetime, timezone
import difflib
import re

BASE_DIR = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE_DIR / "pages"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

app = FastAPI(title="WebPageGenie")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# Expose per-project pages as static under /pages/<slug>/index.html
app.mount("/pages", StaticFiles(directory=PAGES_DIR), name="project_pages")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ws_manager = ReloadWebSocketManager()
_langgraph_app = build_graph()


class ChatRequest(BaseModel):
    message: str
    page_slug: str | None = None
    retrieval_method: str | None = None  # vector | hybrid
    selected_html: str | None = None
    selected_path: list[str] | None = None
    system_context: str | None = None


@app.on_event("startup")
async def on_startup() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    _migrate_flat_pages_to_dirs()
    _migrate_all_version_filenames()
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
    # Ensure legacy files are migrated so links work
    _migrate_flat_pages_to_dirs()
    # List directories containing index.html as page slugs
    dirs = [d.name for d in PAGES_DIR.iterdir() if d.is_dir() and (d / "index.html").exists()]
    # Also include legacy top-level *.html files (shown once)
    legacy = [p.stem for p in PAGES_DIR.glob("*.html")]
    pages = sorted(set(dirs + legacy))
    return templates.TemplateResponse("index.html", {"request": request, "pages": pages})


@app.get("/page", response_class=HTMLResponse)
async def get_page(id: str):
    """Legacy route: serve /page?id=slug for top-level *.html files.
    Prefer /pages/<slug>/index.html going forward.
    """
    # Try new location first
    new_path = PAGES_DIR / id / "index.html"
    if new_path.exists():
        return HTMLResponse(status_code=307, headers={"Location": f"/pages/{id}/index.html"})
    # Fallback to legacy flat file
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
    state = GraphState(
        question=req.message,
        page_slug=req.page_slug,
        retrieval_method=(req.retrieval_method or "vector"),
        selected_html=(req.selected_html or None),
        selected_path=(req.selected_path or []),
        system_context=(req.system_context or None),
    )
    result = _langgraph_app.invoke(state)
    answer = result["answer"] if isinstance(result, dict) else result.answer
    timings = result["timings"] if isinstance(result, dict) else getattr(result, "timings", {})

    saved = False
    if "<html" in (answer or "") and req.page_slug:
        out_path = _save_version_and_write_current(req.page_slug, answer or "")
        try:
            ingest_single_page(out_path)
        except Exception:
            pass
        await ws_manager.broadcast_reload()
        saved = True

    # Persist run log
    try:
        from json import dumps
        retrieved = result["retrieved"] if isinstance(result, dict) else getattr(result, "retrieved", [])
        retrieved_slim = [
            {
                "slug": d.metadata.get("slug"),
                "chunk_id": d.metadata.get("chunk_id"),
                "dom_path": d.metadata.get("dom_path"),
                "preview": (d.page_content[:200] + "…") if d.page_content and len(d.page_content) > 200 else d.page_content,
            }
            for d in retrieved
        ]
        with SessionLocal() as db:
            log = RunLog(
                question=req.message,
                page_slug=req.page_slug,
                retrieval_method=state.retrieval_method,
                retrieved_json=dumps(retrieved_slim),
                timings_json=dumps(timings or {}),
                answer_preview=(answer[:500] + "…") if answer and len(answer) > 500 else answer,
                saved=saved,
            )
            db.add(log)
            db.commit()
    except Exception:
        pass

    return {"saved": saved, "answer": answer, "timings": timings, "retrieval_method": state.retrieval_method}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    async def sse(event: str, data: str) -> str:
        return f"event: {event}\ndata: {data}\n\n"

    async def gen():
        import json
        from asyncio import to_thread

        state = GraphState(
            question=req.message,
            page_slug=req.page_slug,
            retrieval_method=(req.retrieval_method or "vector"),
        )

        yield await sse("started", json.dumps({"message": "received"}))
        yield await sse("phase", json.dumps({"name": "embedding+retrieve"}))
        try:
            state_after_retrieve = await to_thread(rag_retrieve, state)
            yield await sse("retrieved", json.dumps({
                "timings": state_after_retrieve.timings,
                "num_chunks": len(state_after_retrieve.retrieved or []),
            }))
        except Exception as e:
            yield await sse("error", json.dumps({"message": str(e)}))
            return

        yield await sse("phase", json.dumps({"name": "generate"}))
        try:
            state_after_generate = await to_thread(rag_generate, state_after_retrieve)  # type: ignore
            answer = state_after_generate.answer
            timings = state_after_generate.timings
        except Exception as e:
            yield await sse("error", json.dumps({"message": str(e)}))
            return

        saved = False
        if "<html" in (answer or "") and req.page_slug:
            out_path = _save_version_and_write_current(req.page_slug, answer or "")
            try:
                ingest_single_page(out_path)
            except Exception:
                pass
            await ws_manager.broadcast_reload()
            saved = True

        payload = {
            "saved": saved,
            "answer": answer,
            "timings": timings,
            "retrieval_method": state.retrieval_method,
        }
        yield await sse("done", json.dumps(payload))

    from starlette.responses import StreamingResponse
    return StreamingResponse(gen(), media_type="text/event-stream")


def _save_version_and_write_current(slug: str, html_content: str) -> Path:
    out_dir = PAGES_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    versions_dir = out_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        try:
            # Determine next sequential version number using files named v.<n>.html
            existing = []
            for p in versions_dir.glob("v.*.html"):
                m = re.match(r"^v\.(\d+)\.html$", p.name)
                if m:
                    try:
                        existing.append(int(m.group(1)))
                    except Exception:
                        continue
            # Include legacy timestamps when computing next version
            for p in versions_dir.glob("*.html"):
                if p.name.startswith("v."):
                    continue
                if re.match(r"^\d{8}T\d{6}Z\.html$", p.name):
                    existing.append(0)
            next_n = (max(existing) if existing else 0) + 1
            prev = out_path.read_text(encoding="utf-8")
            version_name = f"v.{next_n}"
            version_path = versions_dir / f"{version_name}.html"
            version_path.write_text(prev, encoding="utf-8")
            diff_text = "\n".join(
                difflib.unified_diff(
                    prev.splitlines(),
                    html_content.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                )
            )
            (versions_dir / f"{version_name}.diff.txt").write_text(diff_text, encoding="utf-8")
        except Exception:
            pass

    reload_snippet = (
        "<script>(function(){try{var proto=location.protocol==='https:'?'wss':'ws';var ws=new WebSocket(proto+'://'+location.host+'/ws');ws.onmessage=function(e){if(e.data==='reload'){location.reload();}}}catch(e){}})();</script>"
    )
    content_out = (html_content or "") + "\n" + reload_snippet
    out_path.write_text(content_out, encoding="utf-8")
    return out_path


@app.get("/api/versions")
async def list_versions(slug: str):
    out_dir = PAGES_DIR / slug
    versions_dir = out_dir / "versions"
    versions: list[str] = []
    if (out_dir / "index.html").exists():
        versions.append("current")
    if versions_dir.exists():
        # Only include numbered versions v.<n>, newest first
        numbered: list[tuple[int, str]] = []
        for p in versions_dir.glob("v.*.html"):
            m = re.match(r"^v\.(\d+)\.html$", p.name)
            if m:
                try:
                    n = int(m.group(1))
                    numbered.append((n, f"v.{n}"))
                except Exception:
                    continue
        for _n, name in sorted(numbered, key=lambda t: t[0], reverse=True):
            versions.append(name)
    # Current label is next version number (latest + 1)
    latest = 0
    for v in versions:
        m = re.match(r"^v\.(\d+)$", v)
        if m:
            latest = max(latest, int(m.group(1)))
    current_label = f"v.{latest + 1}"
    return JSONResponse({"slug": slug, "versions": versions, "current": current_label})


def _migrate_all_version_filenames() -> None:
    """Rename legacy timestamped version files to sequential v.<n> per page directory."""
    try:
        for d in PAGES_DIR.iterdir():
            if not d.is_dir():
                continue
            _migrate_version_filenames_for_slug(d.name)
    except Exception:
        pass


def _migrate_version_filenames_for_slug(slug: str) -> None:
    out_dir = PAGES_DIR / slug
    versions_dir = out_dir / "versions"
    if not versions_dir.exists():
        return
    try:
        # Find existing highest v.N
        max_n = 0
        for p in versions_dir.glob("v.*.html"):
            m = re.match(r"^v\.(\d+)\.html$", p.name)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except Exception:
                    continue
        # Collect timestamped files
        ts_files = []
        for p in versions_dir.glob("*.html"):
            if p.name.startswith("v."):
                continue
            m = re.match(r"^(\d{8}T\d{6}Z)\.html$", p.name)
            if m:
                ts_files.append((m.group(1), p))
        # Sort by timestamp string ascending so older gets lower v.N
        ts_files.sort(key=lambda t: t[0])
        for i, (_ts, html_path) in enumerate(ts_files, start=1):
            max_n += 1
            base = f"v.{max_n}"
            new_html = versions_dir / f"{base}.html"
            new_diff = versions_dir / f"{base}.diff.txt"
            try:
                # Rename HTML
                if not new_html.exists():
                    html_path.rename(new_html)
                else:
                    # Fallback copy if name collision
                    new_html.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
                    html_path.unlink(missing_ok=True)
                # Rename matching diff if present
                old_diff = versions_dir / f"{_ts}.diff.txt"
                if old_diff.exists() and not new_diff.exists():
                    old_diff.rename(new_diff)
            except Exception:
                continue
    except Exception:
        pass


def _migrate_flat_pages_to_dirs() -> None:
    """One-time migration: move pages/*.html to pages/<slug>/index.html."""
    try:
        for p in PAGES_DIR.glob("*.html"):
            slug = p.stem
            target_dir = PAGES_DIR / slug
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / "index.html"
            # If destination already exists, skip
            if target_path.exists():
                continue
            try:
                content = p.read_text(encoding="utf-8")
                # Keep original content; live reload snippet will be injected on next save
                target_path.write_text(content, encoding="utf-8")
                p.unlink(missing_ok=True)
            except Exception:
                # Best-effort only
                pass
    except Exception:
        pass


@app.get("/api/runs", response_class=PlainTextResponse)
async def list_runs():
    # Minimal plaintext listing for now
    try:
        from json import loads
        with SessionLocal() as db:
            rows = db.query(RunLog).order_by(RunLog.id.desc()).limit(50).all()
        lines = []
        for r in rows:
            lines.append(f"[{r.id}] {r.created_at} method={r.retrieval_method} slug={r.page_slug} saved={r.saved}")
            lines.append(f"Q: {r.question}")
            lines.append(f"A: {(r.answer_preview or '')[:200]}")
            lines.append(f"retrieved: {r.retrieved_json}")
            lines.append(f"timings: {r.timings_json}")
            lines.append("")
        return "\n".join(lines) or "No runs yet."
    except Exception as e:
        return PlainTextResponse(status_code=500, content=str(e))
