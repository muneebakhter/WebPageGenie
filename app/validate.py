from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urljoin
from pathlib import Path
import os
import httpx


def validate_page_with_playwright(url: str, timeout_ms: int = 10000) -> Dict[str, Any]:
    """
    Best-effort validation using Playwright (if installed).
    - Navigates to the given URL
    - Captures page content (DOM) and console errors
    - Returns a dict with keys: { dom, console_errors, page_errors }

    If Playwright is not installed or fails, returns an empty-result payload.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return {"dom": "", "console_errors": [], "page_errors": [], "ok": False, "reason": "playwright_not_available"}

    console_errors: List[str] = []
    page_errors: List[str] = []
    html: str = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            def on_console(msg):
                try:
                    if msg.type in ("error", "warning"):
                        text = msg.text
                        if text:
                            console_errors.append(text)
                except Exception:
                    pass

            def on_page_error(err):
                try:
                    page_errors.append(str(err))
                except Exception:
                    pass

            page.on("console", on_console)
            page.on("pageerror", on_page_error)

            page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                # Also wait for network to be (mostly) idle
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass

            try:
                html = page.content()
            except Exception:
                html = ""

            context.close()
            browser.close()
    except Exception as e:
        return {"dom": html or "", "console_errors": console_errors, "page_errors": page_errors + [str(e)], "ok": False}

    return {"dom": html or "", "console_errors": console_errors, "page_errors": page_errors, "ok": True}


def scrape_site_with_playwright(url: str, timeout_s: int = 20, save_images: bool = False, page_slug: str | None = None) -> Dict[str, Any]:
    """
    Navigate to url, collect DOM, stylesheet and script sources, fetch their contents,
    combine and produce a short summary for prompt context.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return {"error": "playwright_not_available"}

    def _detect_frameworks(text: str) -> List[str]:
        keys = [
            ("bootstrap", ["bootstrap", ".btn", ".container", "data-bs-"]),
            ("tailwind", ["tailwind", "@tailwind", "class=\"container mx-"]),
            ("jquery", ["jquery", "$.ajax", "$(document)"]),
            ("react", ["react", "ReactDOM.render", "createElement("]),
            ("vue", ["vue", "new Vue(", "createApp("]),
        ]
        found: List[str] = []
        lt = text.lower()
        for name, pats in keys:
            for p in pats:
                if p.lower() in lt:
                    found.append(name)
                    break
        return sorted(list(dict.fromkeys(found)))

    html: str = ""
    css_links: List[str] = []
    js_links: List[str] = []
    inline_css: List[str] = []
    inline_js: List[str] = []
    img_info: List[Dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(timeout_s * 1000)
            page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_s * 1000)
            except Exception:
                pass
            html = page.content()
            script = """
            () => ({
              css: Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(l => l.href),
              js: Array.from(document.querySelectorAll('script[src]')).map(s => s.src),
              inlineCss: Array.from(document.querySelectorAll('style')).map(s => s.textContent || ''),
              inlineJs: Array.from(document.querySelectorAll('script:not([src])')).map(s => s.textContent || ''),
              images: Array.from(document.querySelectorAll('img')).map(img => ({src: img.src, alt: img.alt || ''})),
            })
            """
            data = page.evaluate(script)
            css_links = list(data.get("css") or [])
            js_links = list(data.get("js") or [])
            inline_css = list(data.get("inlineCss") or [])
            inline_js = list(data.get("inlineJs") or [])
            img_raw = list(data.get("images") or [])
            context.close(); browser.close()
    except Exception as e:
        return {"error": str(e)}

    # Resolve relative links
    css_links = [urljoin(url, u) for u in css_links]
    js_links = [urljoin(url, u) for u in js_links]
    img_items = [{"url": urljoin(url, (i.get("src") or "")), "alt": (i.get("alt") or "")} for i in (img_raw if 'img_raw' in locals() else [])]

    # Fetch external assets (cap size)
    async def _fetch_many(links: List[str], max_bytes: int = 120_000) -> List[str]:
        texts: List[str] = []
        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for u in links[:8]:
                try:
                    r = await client.get(u)
                    r.raise_for_status()
                    c = r.text
                    if len(c) > max_bytes:
                        c = c[:max_bytes]
                    texts.append(f"/* {u} */\n" + c)
                except Exception:
                    continue
        return texts

    try:
        import asyncio
        ext_css, ext_js = asyncio.run(_gather_css_js(_fetch_many, css_links, js_links))
    except Exception:
        # Fallback sequentially
        ext_css = []
        ext_js = []
        try:
            import anyio
        except Exception:
            pass

    # If asyncio.run with helper not defined above, implement inline
    if not ('ext_css' in locals() and 'ext_js' in locals()):
        try:
            import asyncio
            async def run_fetch():
                c = await _fetch_many(css_links)
                j = await _fetch_many(js_links)
                return c, j
            ext_css, ext_js = asyncio.run(run_fetch())
        except Exception:
            ext_css, ext_js = [], []

    combined_css = ("\n\n".join(ext_css + ["/* inline */\n" + c for c in inline_css]))[:200_000]
    combined_js = ("\n\n".join(ext_js + ["/* inline */\n" + j for j in inline_js]))[:200_000]

    frameworks = _detect_frameworks("\n".join([html, combined_css, combined_js]))
    summary_lines = [
        f"URL: {url}",
        f"Detected frameworks: {', '.join(frameworks) if frameworks else 'none'}",
        f"Stylesheets: {len(css_links)} external, {len(inline_css)} inline",
        f"Scripts: {len(js_links)} external, {len(inline_js)} inline",
        "Design notes: Extract common layout, color tokens, typography scale, and components from DOM and CSS.",
    ]
    summary = "\n".join(summary_lines)

    # Optionally download images locally for AI use
    if save_images and img_items:
        base_dir = Path(__file__).resolve().parent.parent
        out_dir = base_dir / (f"pages/{page_slug}/assets" if page_slug else "pages/assets")
        out_dir.mkdir(parents=True, exist_ok=True)
        async def _fetch_images(items: List[Dict[str, Any]], max_count: int = 12) -> List[Dict[str, Any]]:
            saved: List[Dict[str, Any]] = []
            timeout = httpx.Timeout(15.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                for idx, it in enumerate(items[:max_count]):
                    u = it.get("url") or ""
                    if not u.lower().startswith("http"):
                        continue
                    try:
                        r = await client.get(u)
                        r.raise_for_status()
                        # Save as PNG regardless of source ext; we just stream bytes
                        fname = f"example-img-{idx+1}.png"
                        dest = out_dir / fname
                        with open(dest, "wb") as f:
                            f.write(r.content)
                        saved.append({"path": ("/" + dest.relative_to(base_dir).as_posix()), "alt": it.get("alt") or ""})
                    except Exception:
                        continue
            return saved
        try:
            import asyncio
            img_info = asyncio.run(_fetch_images(img_items))
        except Exception:
            img_info = []

    return {
        "html": html[:200_000],
        "css_combined": combined_css,
        "js_combined": combined_js,
        "frameworks": frameworks,
        "summary": summary,
        "images": img_info,
    }

def _gather_css_js(fetcher, css_links, js_links):
    import asyncio
    async def run():
        c = await fetcher(css_links)
        j = await fetcher(js_links)
        return c, j
    return asyncio.run(run())


