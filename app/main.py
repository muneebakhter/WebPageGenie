from __future__ import annotations

import os
import shutil
import asyncio
from pathlib import Path
from typing import List
import time
import logging

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .db import init_db, SessionLocal, RunLog
from .db import Document
from .rag import build_graph, GraphState
from .rag import _retrieve as rag_retrieve  # type: ignore
from .rag import _generate as rag_generate  # type: ignore
from .ws import ReloadWebSocketManager
from .validate import validate_page_with_playwright_async
from .validate import scrape_site_with_playwright_async
from .validate import consolidate_to_single_file, assert_single_file_no_external
from .ingest import ingest_all_pages, ingest_single_page
from .images import generate_image_file_async
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

# Basic logger for immediate request/step tracing
logger = logging.getLogger("webpagegenie")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.middleware("http")
async def log_requests(request, call_next):
    t0 = time.perf_counter()
    logger.info(f"HTTP START {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        return response
    finally:
        dt = (time.perf_counter() - t0) * 1000.0
        try:
            status = getattr(response, 'status_code', '?')
        except Exception:
            status = '?'
        logger.info(f"HTTP END   {request.method} {request.url.path} status={status} dt_ms={dt:.1f}")


@app.delete("/api/pages")
async def delete_page_api(slug: str):
    logger.info(f"DELETE page start slug={slug}")
    # Remove directory pages/<slug>
    try:
        target_dir = PAGES_DIR / slug
        existed = target_dir.exists()
        if existed:
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception as e:
        logger.exception("DELETE page: filesystem error")
        return JSONResponse(status_code=500, content={"error": str(e)})

    # Remove DB rows
    try:
        with SessionLocal() as db:
            try:
                db.query(Document).filter(Document.slug == slug).delete(synchronize_session=False)
            except Exception:
                pass
            try:
                db.query(RunLog).filter(RunLog.page_slug == slug).delete(synchronize_session=False)
            except Exception:
                pass
            db.commit()
    except Exception as e:
        logger.exception("DELETE page: db error")
        return JSONResponse(status_code=500, content={"error": str(e)})

    logger.info(f"DELETE page done slug={slug}")
    return JSONResponse({"ok": True, "slug": slug})


@app.delete("/api/pages/version")
async def delete_page_version(slug: str, version: str):
    logger.info(f"DELETE version start slug={slug} version={version}")
    target_dir = PAGES_DIR / slug
    versions_dir = target_dir / "versions"
    try:
        if version == "current":
            # Delete index.html and promote latest v.N to current
            index_path = target_dir / "index.html"
            if index_path.exists():
                try:
                    index_path.unlink()
                except Exception:
                    pass
            # Find highest v.N
            best_n = -1
            best_path = None
            for p in versions_dir.glob("v.*.html"):
                m = re.match(r"^v\.(\d+)\.html$", p.name)
                if not m:
                    continue
                try:
                    n = int(m.group(1))
                except Exception:
                    continue
                if n > best_n:
                    best_n = n
                    best_path = p
            promoted_to = None
            if best_path is not None:
                # Move best version to index.html
                content = best_path.read_text(encoding="utf-8")
                (target_dir / "index.html").write_text(content, encoding="utf-8")
                # Remove the version file and its diff if exists
                try:
                    best_path.unlink()
                except Exception:
                    pass
                try:
                    diff = versions_dir / f"v.{best_n}.diff.txt"
                    diff.unlink(missing_ok=True)
                except Exception:
                    pass
                promoted_to = f"v.{best_n}"
            else:
                # No versions remain; clean folder if empty
                try:
                    if not any(target_dir.iterdir()):
                        shutil.rmtree(target_dir, ignore_errors=True)
                except Exception:
                    pass
            # Re-ingest current if present
            try:
                if (target_dir / "index.html").exists():
                    ingest_single_page(target_dir / "index.html")
            except Exception:
                pass
            logger.info(f"DELETE version done slug={slug} version=current promoted_to={promoted_to}")
            return JSONResponse({"ok": True, "slug": slug, "deleted_version": "current", "promoted_to": promoted_to})
        else:
            # Delete a specific v.N
            if not versions_dir.exists():
                return JSONResponse(status_code=404, content={"error": "no versions folder"})
            m = re.match(r"^v\.(\d+)$", version)
            if not m:
                return JSONResponse(status_code=400, content={"error": "version must be 'current' or v.N"})
            html_path = versions_dir / f"{version}.html"
            if not html_path.exists():
                return JSONResponse(status_code=404, content={"error": "version file not found"})
            try:
                html_path.unlink()
            except Exception:
                pass
            try:
                (versions_dir / f"{version}.diff.txt").unlink(missing_ok=True)
            except Exception:
                pass
            logger.info(f"DELETE version done slug={slug} version={version}")
            return JSONResponse({"ok": True, "slug": slug, "deleted_version": version})
    except Exception as e:
        logger.exception("DELETE version error")
        return JSONResponse(status_code=500, content={"error": str(e)})


class ChatRequest(BaseModel):
    message: str
    page_slug: str | None = None
    retrieval_method: str | None = None  # vector | hybrid
    selected_html: str | None = None
    selected_path: list[str] | None = None
    system_context: str | None = None
    reference_url: str | None = None  # URL to scrape for new pages
    extract_images: bool = False  # Extract images from reference site
class ImageRequest(BaseModel):
    prompt: str
    page_slug: str | None = None
    size: str | None = None  # e.g., 1024x1024
    seed: int | None = None
    output_filename: str | None = None

class ValidateRequest(BaseModel):
    slug: str | None = None
    url: str | None = None
    save_images: bool | None = None
    page_slug: str | None = None



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
@app.post("/api/generate_image")
async def api_generate_image(req: ImageRequest):
    try:
        info = await generate_image_file_async(
            prompt=req.prompt,
            page_slug=req.page_slug,
            size=(req.size or "1024x1024"),
            seed=req.seed,
            output_filename=req.output_filename,
        )
        return JSONResponse(info)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/tools/image")
async def api_tool_image(req: ImageRequest):
    try:
        # Implement BFL flow like test.py: create → poll → download sample to output_filename
        logger.info(f"TOOL image: start prompt='{(req.prompt or '')[:80]}' size={req.size} out={req.output_filename}")
        if not req.prompt:
            return JSONResponse(status_code=400, content={"error": "prompt is required"})
        if not req.output_filename or not req.output_filename.lower().endswith(".png"):
            return JSONResponse(status_code=400, content={"error": "output_filename (.png) is required"})

        import httpx, os
        from pathlib import Path

        bfl_key = os.getenv("BFL_API_KEY") or os.getenv("BFL_AI_KEY")
        if not bfl_key:
            return JSONResponse(status_code=500, content={"error": "BFL_API_KEY/BFL_AI_KEY not set"})

        # Aspect ratio from size (e.g., 512x512 -> 1:1)
        ar = "1:1"
        if req.size and "x" in req.size:
            try:
                w, h = [int(x) for x in req.size.lower().split("x", 1)]
                from math import gcd
                g = gcd(max(w,1), max(h,1))
                ar = f"{w//g}:{h//g}"
            except Exception:
                pass

        create_url = "https://api.bfl.ai/v1/flux-kontext-pro"
        headers = {"accept": "application/json", "x-key": bfl_key, "Content-Type": "application/json"}
        payload = {"prompt": req.prompt, "aspect_ratio": ar}

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            r = await client.post(create_url, headers=headers, json=payload)
            r.raise_for_status()
            j = r.json()
            polling_url = j.get("polling_url") or j.get("status_url")
            if not polling_url:
                return JSONResponse(status_code=500, content={"error": "No polling_url in response", "response": j})

        # Poll
        sample_url = None
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            while True:
                r2 = await client.get(polling_url, headers={"accept": "application/json", "x-key": bfl_key})
                r2.raise_for_status()
                st = r2.json()
                status = (st.get("status") or "").lower()
                if status == "ready":
                    # According to test.py: result.sample
                    try:
                        sample_url = st["result"]["sample"]
                    except Exception:
                        pass
                    break
                if (time.perf_counter() - t0) > 180:
                    return JSONResponse(status_code=504, content={"error": "timeout waiting for image", "last": st})
                await asyncio.sleep(1)

        if not sample_url:
            return JSONResponse(status_code=500, content={"error": "no sample url returned"})

        # Download image and save to output_filename
        out_path = Path(req.output_filename)
        if not out_path.is_absolute():
            out_path = (BASE_DIR / out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            img = await client.get(sample_url)
            img.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(img.content)

        # Build URL if under known roots
        if str(out_path).startswith(str(STATIC_DIR)):
            url = "/static/" + out_path.relative_to(STATIC_DIR).as_posix()
        elif str(out_path).startswith(str(PAGES_DIR)):
            url = "/" + out_path.relative_to(BASE_DIR).as_posix()
        else:
            url = out_path.as_posix()

        logger.info(f"TOOL image: saved -> {out_path}")
        return JSONResponse({"ok": True, "path": str(out_path), "url": url, "sample_url": sample_url})
    except Exception as e:
        logger.exception("TOOL image: error")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/tools/validate")
async def api_tool_validate(req: ValidateRequest):
    try:
        if req.url:
            url = req.url
        elif req.slug:
            base_url = os.getenv("BASE_URL", "http://localhost:8000")
            url = f"{base_url}/pages/{req.slug}/index.html"
        else:
            return JSONResponse(status_code=400, content={"error": "Provide slug or url"})
        result = await validate_page_with_playwright_async(url)
        return JSONResponse({"url": url, **result})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/tools/example/scrape")
async def api_tool_example_scrape(req: ValidateRequest):
    try:
        if not req.url:
            return JSONResponse(status_code=400, content={"error": "url required"})
        data = await scrape_site_with_playwright_async(req.url)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# Removed /api/generate_logo endpoint (superseded by /api/generate_image and /api/tools/image)



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
    # Determine if images should be generated based on the message content
    needs_image_generation = any(keyword in req.message.lower() for keyword in ["image:", "generate image", "create image"])
    
    state = GraphState(
        question=req.message,
        page_slug=req.page_slug,
        retrieval_method=(req.retrieval_method or "vector"),
        selected_html=(req.selected_html or None),
        selected_path=(req.selected_path or []),
        system_context=(req.system_context or None),
        reference_url=req.reference_url,
        extract_images=req.extract_images,
        needs_image_generation=needs_image_generation,
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
    try:
        import logging as _logging
        _logging.getLogger("webpagegenie.chat").info(
            "chat request received: page_slug=%s, retrieval=%s, selected_html_len=%s, selected_path_len=%s",
            req.page_slug,
            req.retrieval_method,
            len(req.selected_html or ""),
            len(req.selected_path or []) if isinstance(req.selected_path, list) else 0,
        )
    except Exception:
        pass
    async def sse(event: str, data: str) -> str:
        return f"event: {event}\ndata: {data}\n\n"

    async def gen():
        import json
        from asyncio import to_thread

        # Determine if images should be generated based on the message content
        needs_image_generation = any(keyword in req.message.lower() for keyword in ["image:", "generate image", "create image"])

        state = GraphState(
            question=req.message,
            page_slug=req.page_slug,
            retrieval_method=(req.retrieval_method or "vector"),
            selected_html=req.selected_html,
            selected_path=(req.selected_path or []),
            system_context=req.system_context,
            reference_url=req.reference_url,
            extract_images=req.extract_images,
            needs_image_generation=needs_image_generation,
        )

        yield await sse("started", json.dumps({"message": "received"}))
        
        # Use the full LangGraph workflow instead of individual steps
        try:
            yield await sse("phase", json.dumps({"name": "running enhanced workflow"}))
            final_state = await to_thread(_langgraph_app.invoke, state)
            
            # Extract results from final state
            answer = final_state["answer"] if isinstance(final_state, dict) else final_state.answer
            timings = final_state["timings"] if isinstance(final_state, dict) else getattr(final_state, "timings", {})
            
            yield await sse("completed", json.dumps({
                "timings": timings,
                "validation": getattr(final_state, "validation", None) if not isinstance(final_state, dict) else final_state.get("validation"),
                "scraped_data_available": bool(getattr(final_state, "scraped_data", None) if not isinstance(final_state, dict) else final_state.get("scraped_data")),
                "images_extracted": len(getattr(final_state, "extracted_images", []) if not isinstance(final_state, dict) else final_state.get("extracted_images", [])),
            }))
        except Exception as e:
            yield await sse("error", json.dumps({"message": str(e)}))
        # Handle HTML sanitization and saving
        if answer and "<html" in answer:
            # Sanitize: extract only the HTML document from the model response
            try:
                import re as _re
                raw = answer or ""
                m = _re.search(r"(<html[\s\S]*?</html>)", raw, _re.IGNORECASE)
                if m:
                    answer = m.group(1).strip()
                else:
                    m = _re.search(r"```html[\r\n]+([\s\S]*?)```", raw, _re.IGNORECASE)
                    if m:
                        answer = m.group(1).strip()
            except Exception:
                pass

        saved = False
        if "<html" in (answer or "") and req.page_slug:
            out_path = _save_version_and_write_current(req.page_slug, answer or "")
            try:
                ingest_single_page(out_path)
            except Exception:
                pass
            await ws_manager.broadcast_reload()
            saved = True

        # Prepare final payload
        payload = {
            "saved": saved,
            "answer": answer,
            "timings": timings,
            "retrieval_method": (getattr(final_state, "retrieval_method", None) if not isinstance(final_state, dict) else final_state.get("retrieval_method")) or "vector",
        }
        
        # Include validation summary if present
        try:
            validation = getattr(final_state, "validation", None) if not isinstance(final_state, dict) else final_state.get("validation")
            if validation:
                payload["validation"] = {
                    "console_errors": (validation.get("console_errors") or [])[:10],
                    "page_errors": (validation.get("page_errors") or [])[:10],
                    "single_page_issues": (validation.get("single_page_issues") or [])[:5],
                    "syntax_issues": (validation.get("syntax_issues") or [])[:5],
                    "external_resource_issues": (validation.get("external_resource_issues") or [])[:5],
                    "ok": validation.get("ok", False),
                }
        except Exception:
            pass
            
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
