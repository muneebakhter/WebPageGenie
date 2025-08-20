from __future__ import annotations

import os
from typing import Any, Dict, List
import logging
import json
import asyncio
from pathlib import Path

from langchain_openai import ChatOpenAI
from openai import OpenAI
from langchain_core.documents import Document as LCDocument
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from .db import SessionLocal
from .vectors import similarity_search, hybrid_search_rrf
from .validate import validate_page_with_playwright, scrape_site_with_playwright_async, consolidate_to_single_file
from .images import generate_image_file_async

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
    # New fields for enhanced workflow
    is_new_page: bool = False
    reference_url: str | None = None
    extract_images: bool = False
    scraped_data: Dict[str, Any] | None = None
    extracted_images: List[Dict[str, Any]] = Field(default_factory=list)
    needs_image_generation: bool = False


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
    # Choose model dynamically: edits use gpt-4o-mini; first-time create uses gpt-5
    try:
        base_dir = os.getenv("PAGES_DIR", "pages")
        slug_dir = os.path.join(base_dir, state.page_slug or "") if state.page_slug else None
        is_existing_page = bool(slug_dir and os.path.exists(os.path.join(slug_dir, "index.html")))
    except Exception:
        is_existing_page = False
    selected_model = "gpt-4o-mini" if is_existing_page else "gpt-5"
    llm = ChatOpenAI(api_key=OPENAI_API_KEY, model=selected_model, temperature=1)
    context = "\n\n".join(d.page_content for d in state.retrieved)
    # Fallback: if nothing retrieved but a page slug exists, load current page files from disk
    if not context and state.page_slug:
        try:
            base_dir = os.getenv("PAGES_DIR", "pages")
            slug_dir = os.path.join(base_dir, state.page_slug)
            html_path = os.path.join(slug_dir, "index.html")
            html_content = ""
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
                    html_content = f.read()
            css_js_bundle: list[str] = []
            # Naive parse for local CSS/JS references and inline their contents (best-effort)
            try:
                from bs4 import BeautifulSoup  # type: ignore
                soup = BeautifulSoup(html_content, "html.parser")
                links = soup.find_all("link", rel=lambda v: (v or "").lower() == "stylesheet")
                for link in links:
                    href = (link.get("href") or "").strip()
                    if href and not href.startswith(("http://", "https://", "//")):
                        p = os.path.normpath(os.path.join(slug_dir, href))
                        if os.path.exists(p) and p.startswith(slug_dir):
                            try:
                                with open(p, "r", encoding="utf-8", errors="ignore") as cf:
                                    css_js_bundle.append(f"/* {href} */\n" + cf.read())
                            except Exception:
                                pass
                scripts = soup.find_all("script")
                for s in scripts:
                    src = (s.get("src") or "").strip()
                    if src and not src.startswith(("http://", "https://", "//")):
                        p = os.path.normpath(os.path.join(slug_dir, src))
                        if os.path.exists(p) and p.startswith(slug_dir):
                            try:
                                with open(p, "r", encoding="utf-8", errors="ignore") as jf:
                                    css_js_bundle.append(f"// {src}\n" + jf.read())
                            except Exception:
                                pass
            except Exception:
                pass
            assets = ("\n\n" + "\n\n".join(css_js_bundle)) if css_js_bundle else ""
            context = (html_content or "") + assets
            logging.getLogger("webpagegenie.context").info(
                "Loaded page files for slug=%s: html=%s bytes, assets=%d items",
                state.page_slug,
                len(html_content or ""),
                len(css_js_bundle),
            )
        except Exception:
            pass
    # Enhanced system prompt based on page type
    default_context = (
        "You are an expert frontend developer familiar with the latest frontend JS frameworks and tasked as a contractor to create SPAs with enterprise-grade professional designs. "
        "Make modern-looking pages with tasteful graphics, subtle animations, and modals where appropriate. Here is your task from the client:"
    )
    
    if state.is_new_page and state.scraped_data:
        # New page with reference website
        system = (
            (state.system_context or default_context) + "\n\n"
            "You are WebPageGenie, creating a NEW webpage following the design and structure of a reference site. "
            "Use the scraped reference data below to maintain similar layout, styling approach, and JavaScript libraries. "
            "Create a single-file HTML document with all CSS and JS inline. Use Bootstrap 5 as the base framework. "
            "If images were extracted, incorporate them appropriately into the design with proper alt text."
        )
    elif state.is_new_page:
        # New page without reference
        system = (
            (state.system_context or default_context) + "\n\n"
            "You are WebPageGenie, creating a NEW single-file HTML5/CSS3/JS webpage from scratch. "
            "Create a modern, professional design with Bootstrap 5. Include all CSS and JS inline. "
            "Make it visually appealing with proper responsive design."
        )
    else:
        # Existing page editing
        system = (
            (state.system_context or default_context) + "\n\n"
            "You are WebPageGenie, editing an EXISTING webpage. "
            "Prefer small, targeted edits to preserve the existing structure, styles, and functionality. "
            "Only replace or add the minimal necessary sections. Ensure compatibility with existing assets. "
            "If new libraries or scripts are needed, inline them in the header and minify when possible."
        )
    
    selected_block = (state.selected_html or "").strip()
    
    # Build user prompt based on page type and available data
    if state.page_slug:
        parts: List[str] = []
        parts.append(f"Task: {state.question}\n\n")
        
        # Add scraped reference data for new pages
        if state.is_new_page and state.scraped_data:
            scraped = state.scraped_data
            parts.append("Reference website analysis:\n")
            parts.append(f"Summary: {scraped.get('summary', 'No summary available')}\n")
            parts.append(f"Detected frameworks: {', '.join(scraped.get('frameworks', []))}\n")
            parts.append(f"HTML structure (truncated): {scraped.get('html', '')[:2000]}...\n")
            if scraped.get('css_combined'):
                parts.append(f"CSS patterns: {scraped['css_combined'][:1000]}...\n")
            if state.extracted_images:
                parts.append(f"Extracted images ({len(state.extracted_images)}):\n")
                for img in state.extracted_images:
                    parts.append(f"- {img.get('path', 'unknown')} (alt: {img.get('alt', 'no alt')})\n")
            parts.append("\n")
        
        if not state.is_new_page:
            # For existing pages, show current content
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
            
        # Output requirements
        if state.is_new_page:
            parts.append(
                "Output requirement:\n"
                "- Create a complete, valid SINGLE-FILE HTML document following the reference design (if provided).\n"
                "- ALL CSS and JS must be inline within the HTML. One file only.\n"
                "- Use Bootstrap 5 for styling (inline the CSS). Include appropriate frontend libraries inline.\n"
                "- Ensure responsive design and professional appearance.\n"
                "- If images were extracted, place them appropriately with proper alt text.\n"
            )
        else:
            parts.append(
                "Output requirement:\n"
                "- Return a complete, valid SINGLE-FILE HTML document with your edits.\n"
                "- ALL CSS and JS must be inline within the HTML. One file only.\n"
                "- Preserve existing structure and only modify what's necessary.\n"
                "- If adding new libraries, inline them in the header and minify when possible.\n"
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

    # Log the exact request body that would be sent to OpenAI chat/completions
    openai_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    logging.getLogger("webpagegenie.openai").info(
        "OpenAI chat.completions request: %s",
        json.dumps(openai_payload, ensure_ascii=False),
    )

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


def _check_page_status(state: GraphState) -> GraphState:
    """Determine if this is a new page or existing page workflow"""
    import time
    from pathlib import Path
    
    t0 = time.perf_counter()
    
    # If page_slug is provided, check if it's an existing page
    if state.page_slug:
        base_dir = Path(__file__).resolve().parent.parent
        page_dir = base_dir / "pages" / state.page_slug
        current_page = page_dir / "index.html"
        
        # If the page doesn't exist, it's a new page
        state.is_new_page = not current_page.exists()
    else:
        # No page slug means this is likely a general query, treat as new page workflow
        state.is_new_page = True
    
    t1 = time.perf_counter()
    state.timings = {**(state.timings or {}), "check_page_ms": (t1 - t0) * 1000.0}
    return state


async def _scrape_reference_async(state: GraphState) -> GraphState:
    """Scrape reference website if URL is provided for new pages"""
    import time
    
    if not state.reference_url or not state.is_new_page:
        return state
        
    t0 = time.perf_counter()
    
    try:
        # Use the async scraping function
        scraped_data = await scrape_site_with_playwright_async(
            url=state.reference_url,
            save_images=state.extract_images,
            page_slug=state.page_slug
        )
        state.scraped_data = scraped_data
        
        if state.extract_images and scraped_data.get("images"):
            state.extracted_images = scraped_data["images"]
            
    except Exception as e:
        logging.getLogger("webpagegenie.scrape").error(f"Scraping failed: {e}")
        state.scraped_data = {"error": str(e)}
    
    t1 = time.perf_counter()
    state.timings = {**(state.timings or {}), "scrape_ms": (t1 - t0) * 1000.0}
    return state


def _scrape_reference(state: GraphState) -> GraphState:
    """Synchronous wrapper for scraping"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in an async context, create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _scrape_reference_async(state))
                return future.result()
        else:
            return asyncio.run(_scrape_reference_async(state))
    except Exception as e:
        logging.getLogger("webpagegenie.scrape").error(f"Scraping wrapper failed: {e}")
        return state


async def _handle_images_async(state: GraphState) -> GraphState:
    """Handle image generation and placement for new images mentioned in the prompt"""
    import time
    import re
    
    if not state.needs_image_generation:
        return state
        
    t0 = time.perf_counter()
    
    # Look for image generation requests in the question
    image_patterns = [
        r"image[:\s]+([^\.]+)",
        r"generate.+image.+of\s+([^\.]+)",
        r"create.+image.+showing\s+([^\.]+)"
    ]
    
    for pattern in image_patterns:
        matches = re.findall(pattern, state.question, re.IGNORECASE)
        for match in matches:
            try:
                prompt = match.strip()
                if len(prompt) > 10:  # Only generate for substantial prompts
                    result = await generate_image_file_async(
                        prompt=prompt,
                        page_slug=state.page_slug
                    )
                    if result.get("saved"):
                        state.extracted_images.append({
                            "path": result["path"],
                            "url": result["url"],
                            "alt": prompt,
                            "generated": True
                        })
            except Exception as e:
                logging.getLogger("webpagegenie.images").error(f"Image generation failed: {e}")
    
    t1 = time.perf_counter()
    state.timings = {**(state.timings or {}), "images_ms": (t1 - t0) * 1000.0}
    return state


def _handle_images(state: GraphState) -> GraphState:
    """Synchronous wrapper for image handling"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _handle_images_async(state))
                return future.result()
        else:
            return asyncio.run(_handle_images_async(state))
    except Exception as e:
        logging.getLogger("webpagegenie.images").error(f"Image handling wrapper failed: {e}")
        return state


def _move_images_to_page_dir(state: GraphState) -> GraphState:
    """Move extracted/generated images to the page directory and update paths in HTML"""
    import time
    import shutil
    import re
    from pathlib import Path
    
    if not state.page_slug or not state.extracted_images:
        return state
        
    t0 = time.perf_counter()
    
    try:
        base_dir = Path(__file__).resolve().parent.parent
        page_assets_dir = base_dir / "pages" / state.page_slug / "assets"
        page_assets_dir.mkdir(parents=True, exist_ok=True)
        
        # Track moved images for path updates
        moved_images = []
        
        for img in state.extracted_images:
            src_path = Path(img.get("path", ""))
            if src_path.exists():
                # Generate new filename
                dest_filename = f"{src_path.stem}_{int(time.time())}{src_path.suffix}"
                dest_path = page_assets_dir / dest_filename
                
                try:
                    shutil.copy2(src_path, dest_path)
                    # Update relative path for HTML
                    relative_path = f"assets/{dest_filename}"
                    moved_images.append({
                        "old_path": str(src_path),
                        "new_path": str(dest_path), 
                        "relative_path": relative_path,
                        "alt": img.get("alt", "")
                    })
                except Exception as e:
                    logging.getLogger("webpagegenie.images").error(f"Failed to move image {src_path}: {e}")
        
        # Update the state with moved image info
        if moved_images:
            state.extracted_images = moved_images
            
    except Exception as e:
        logging.getLogger("webpagegenie.images").error(f"Image moving failed: {e}")
    
    t1 = time.perf_counter()
    state.timings = {**(state.timings or {}), "move_images_ms": (t1 - t0) * 1000.0}
    return state


def _enhanced_validate(state: GraphState) -> GraphState:
    """Enhanced validation with single-page check, console errors, and syntax validation"""
    import time
    import re
    from pathlib import Path
    
    if not state.page_slug:
        return state
        
    t0 = time.perf_counter()
    
    try:
        # Check if HTML is single-page (no external links to other HTML files)
        html_content = state.answer or ""
        
        # Look for problematic multi-page patterns
        multi_page_patterns = [
            r'href\s*=\s*["\'][^"\']*\.html["\']',  # Links to other HTML files
            r'window\.location\s*=\s*["\'][^"\']*\.html["\']',  # JS redirects to HTML
            r'location\.href\s*=\s*["\'][^"\']*\.html["\']'
        ]
        
        multi_page_issues = []
        for pattern in multi_page_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            if matches:
                multi_page_issues.extend(matches)
        
        # Basic syntax validation
        syntax_issues = []
        
        # Check for unclosed tags
        open_tags = re.findall(r'<(\w+)(?:\s[^>]*)?>(?![^<]*</\1>)', html_content)
        void_elements = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
        unclosed_tags = [tag for tag in open_tags if tag.lower() not in void_elements]
        if unclosed_tags:
            syntax_issues.append(f"Potentially unclosed tags: {', '.join(set(unclosed_tags))}")
        
        # Check for inline CSS/JS requirement compliance
        external_links = re.findall(r'<link[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']*)["\']', html_content)
        external_scripts = re.findall(r'<script[^>]*src=["\']([^"\']*)["\']', html_content)
        
        external_issues = []
        if external_links:
            external_issues.append(f"External CSS found: {', '.join(external_links)}")
        if external_scripts:
            external_issues.append(f"External scripts found: {', '.join(external_scripts)}")
        
        # Store validation results
        validation_result = {
            "single_page_issues": multi_page_issues,
            "syntax_issues": syntax_issues,
            "external_resource_issues": external_issues,
            "ok": len(multi_page_issues) == 0 and len(syntax_issues) == 0 and len(external_issues) == 0
        }
        
        # Also run the existing playwright validation if possible
        try:
            base_url = os.getenv("BASE_URL", "http://localhost:8000")
            url = f"{base_url}/pages/{state.page_slug}/index.html"
            playwright_result = validate_page_with_playwright(url)
            validation_result.update(playwright_result)
        except Exception as e:
            validation_result["playwright_error"] = str(e)
        
        state.validation = validation_result
        state.validation_attempts = int(getattr(state, "validation_attempts", 0) or 0) + 1
        
    except Exception as e:
        logging.getLogger("webpagegenie.validate").error(f"Enhanced validation failed: {e}")
        state.validation = {"error": str(e), "ok": False}
    
    t1 = time.perf_counter()
    state.timings = {**(state.timings or {}), "enhanced_validate_ms": (t1 - t0) * 1000.0}
    return state


def build_graph():
    graph = StateGraph(GraphState)
    
    # Add all nodes
    graph.add_node("check_page_status", _check_page_status)
    graph.add_node("scrape_reference", _scrape_reference)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("handle_images", _handle_images)
    graph.add_node("generate", _generate)
    graph.add_node("move_images", _move_images_to_page_dir)
    graph.add_node("enhanced_validate", _enhanced_validate)
    
    # Legacy validation for backwards compatibility
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

    # Conditional routing functions
    def _route_after_check(state: GraphState) -> str:
        """Route to scraping if new page with reference URL, otherwise to retrieve"""
        if state.is_new_page and state.reference_url:
            return "scrape_reference"
        return "retrieve"
    
    def _route_after_scrape(state: GraphState) -> str:
        """After scraping, go to retrieve"""
        return "retrieve"
    
    def _route_after_retrieve(state: GraphState) -> str:
        """After retrieve, check if we need image handling"""
        if state.needs_image_generation or (state.is_new_page and "image" in state.question.lower()):
            return "handle_images"
        return "generate"
    
    def _route_after_images(state: GraphState) -> str:
        """After image handling, go to generate"""
        return "generate"
    
    def _route_after_generate(state: GraphState) -> str:
        """After generation, move images if needed"""
        if state.extracted_images and state.page_slug:
            return "move_images"
        return "enhanced_validate"
    
    def _route_after_move_images(state: GraphState) -> str:
        """After moving images, go to validation"""
        return "enhanced_validate"

    def _needs_fix(state: GraphState) -> str:
        """Enhanced needs fix logic"""
        v = getattr(state, "validation", None) or {}
        
        # Check all types of issues
        console_errors = v.get("console_errors") or []
        page_errors = v.get("page_errors") or []
        single_page_issues = v.get("single_page_issues") or []
        syntax_issues = v.get("syntax_issues") or []
        external_issues = v.get("external_resource_issues") or []
        
        all_errors = console_errors + page_errors + single_page_issues + syntax_issues + external_issues
        
        # If there are errors and we have not exceeded attempts, go back to generate
        attempts = int(getattr(state, "validation_attempts", 0) or 0)
        MAX_ATTEMPTS = 3  # Increased for more thorough fixing
        
        if all_errors and attempts <= MAX_ATTEMPTS:
            # Add error context to the state for the next generation
            error_context = "Previous validation found these issues to fix:\n"
            for err in all_errors[:10]:  # Limit to avoid token overflow
                error_context += f"- {err}\n"
            
            # Store error context in the question for next iteration
            if not hasattr(state, '_original_question'):
                state._original_question = state.question
            state.question = f"{state._original_question}\n\nIMPORTANT - Fix these validation errors:\n{error_context}"
            
            return "generate"
        
        # Restore original question if we had modified it
        if hasattr(state, '_original_question'):
            state.question = state._original_question
        
        return END

    # Build the graph structure
    graph.set_entry_point("check_page_status")
    
    # From check_page_status, route to scrape or retrieve
    graph.add_conditional_edges("check_page_status", _route_after_check, {
        "scrape_reference": "scrape_reference",
        "retrieve": "retrieve"
    })
    
    # From scrape_reference, go to retrieve
    graph.add_edge("scrape_reference", "retrieve")
    
    # From retrieve, route to images or generate
    graph.add_conditional_edges("retrieve", _route_after_retrieve, {
        "handle_images": "handle_images",
        "generate": "generate"
    })
    
    # From handle_images, go to generate
    graph.add_edge("handle_images", "generate")
    
    # From generate, route to move_images or validate
    graph.add_conditional_edges("generate", _route_after_generate, {
        "move_images": "move_images",
        "enhanced_validate": "enhanced_validate"
    })
    
    # From move_images, go to validate
    graph.add_edge("move_images", "enhanced_validate")
    
    # From validate, either fix or end
    graph.add_conditional_edges("enhanced_validate", _needs_fix, {
        "generate": "generate",
        END: END
    })
    
    return graph.compile()
