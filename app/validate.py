from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urljoin
from pathlib import Path
import os
import httpx
from bs4 import BeautifulSoup  # type: ignore


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


# Async variants to avoid using Playwright sync API inside asyncio loops
async def validate_page_with_playwright_async(url: str, timeout_ms: int = 10000) -> Dict[str, Any]:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return {"dom": "", "console_errors": [], "page_errors": [], "ok": False, "reason": "playwright_not_available"}

    console_errors: List[str] = []
    page_errors: List[str] = []
    html: str = ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def on_console(msg):
                try:
                    if msg.type in ("error", "warning"):
                        t = msg.text
                        if t:
                            console_errors.append(t)
                except Exception:
                    pass

            async def on_page_error(err):
                try:
                    page_errors.append(str(err))
                except Exception:
                    pass

            page.on("console", on_console)
            page.on("pageerror", on_page_error)

            await page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass
            try:
                html = await page.content()
            except Exception:
                html = ""
            await context.close(); await browser.close()
    except Exception as e:
        return {"dom": html or "", "console_errors": console_errors, "page_errors": page_errors + [str(e)], "ok": False}

    return {"dom": html or "", "console_errors": console_errors, "page_errors": page_errors, "ok": True}


async def scrape_site_with_playwright_async(url: str, timeout_s: int = 20, save_images: bool = False, page_slug: str | None = None) -> Dict[str, Any]:
    try:
        from playwright.async_api import async_playwright  # type: ignore
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
    img_raw: List[Dict[str, Any]] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(timeout_s * 1000)
            await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_s * 1000)
            except Exception:
                pass
            html = await page.content()
            script = """
            () => ({
              css: Array.from(document.querySelectorAll('link[rel="stylesheet"]').map(l => l.href)),
              js: Array.from(document.querySelectorAll('script[src]').map(s => s.src)),
              inlineCss: Array.from(document.querySelectorAll('style')).map(s => s.textContent || ''),
              inlineJs: Array.from(document.querySelectorAll('script:not([src])')).map(s => s.textContent || ''),
              images: Array.from(document.querySelectorAll('img')).map(img => ({src: img.src, alt: img.alt || ''})),
            })
            """
            data = await page.evaluate(script)
            css_links = list(data.get("css") or [])
            js_links = list(data.get("js") or [])
            inline_css = list(data.get("inlineCss") or [])
            inline_js = list(data.get("inlineJs") or [])
            img_raw = list(data.get("images") or [])
            await context.close(); await browser.close()
    except Exception as e:
        return {"error": str(e)}

    # Resolve relative links
    css_links = [urljoin(url, u) for u in css_links]
    js_links = [urljoin(url, u) for u in js_links]
    img_items = [{"url": urljoin(url, (i.get("src") or "")), "alt": (i.get("alt") or "")} for i in img_raw]

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
        ext_css, ext_js = await asyncio.gather(_fetch_many(css_links), _fetch_many(js_links))
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
                        fname = f"example-img-{idx+1}.png"
                        dest = out_dir / fname
                        with open(dest, "wb") as f:
                            f.write(r.content)
                        saved.append({"path": ("/" + dest.relative_to(base_dir).as_posix()), "alt": it.get("alt") or ""})
                    except Exception:
                        continue
            return saved
        try:
            img_info = await _fetch_images(img_items)
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


async def consolidate_to_single_file(slug: str) -> Dict[str, Any]:
    """
    Ensure page under pages/<slug>/index.html is a single self-contained HTML file:
    - Inline local CSS <link rel="stylesheet"> as <style>
    - Inline local JS <script src> as <script>
    - Check <img src>; if broken local path, try to use assets under pages/<slug>/assets;
      if none available, call image tool to generate a placeholder using alt text
    Returns details and whether changes were made.
    """
    base_dir = Path(__file__).resolve().parent.parent
    slug_dir = base_dir / "pages" / slug
    html_path = slug_dir / "index.html"
    result: Dict[str, Any] = {
        "changed": False,
        "inlined_css": 0,
        "inlined_js": 0,
        "fixed_images": 0,
    }
    if not html_path.exists():
        return result
    try:
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return result

    soup = BeautifulSoup(html_text, "html.parser")
    changed = False

    # Inline local and external stylesheets (including approved libs like Mermaid/Chart.js if linked)
    for link in list(soup.find_all("link")):
        rel = (link.get("rel") or [""])
        if any((r or "").lower() == "stylesheet" for r in rel):
            href = (link.get("href") or "").strip()
            try:
                css_text = None
                if href and href.startswith(("http://", "https://")):
                    try:
                        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
                            r = await client.get(href)
                            r.raise_for_status()
                            css_text = r.text
                    except Exception:
                        css_text = ""
                elif href and not href.startswith("//"):
                    p = (slug_dir / href).resolve()
                    if p.exists() and str(p).startswith(str(slug_dir.resolve())):
                        css_text = p.read_text(encoding="utf-8", errors="ignore")
                    else:
                        css_text = ""
                if css_text is not None:
                    style = soup.new_tag("style")
                    style.string = css_text
                    link.replace_with(style)
                    result["inlined_css"] += 1
                    changed = True
            except Exception:
                continue

    # Inline local and external scripts (including approved libs like Mermaid/Chart.js if linked)
    for script in list(soup.find_all("script")):
        src = (script.get("src") or "").strip()
        if not src:
            continue
        try:
            js_text = None
            if src.startswith(("http://", "https://")):
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
                        r = await client.get(src)
                        r.raise_for_status()
                        js_text = r.text
                except Exception:
                    js_text = ""
            elif not src.startswith("//"):
                p = (slug_dir / src).resolve()
                if p.exists() and str(p).startswith(str(slug_dir.resolve())):
                    js_text = p.read_text(encoding="utf-8", errors="ignore")
                else:
                    js_text = ""
            if js_text is not None:
                new_script = soup.new_tag("script")
                new_script.string = js_text
                script.replace_with(new_script)
                result["inlined_js"] += 1
                changed = True
        except Exception:
            continue

    # Fix broken images (convert to local assets and ensure single-file viability)
    assets_dir = slug_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    img_tags = list(soup.find_all("img"))
    fix_count = 0
    for idx, img in enumerate(img_tags, start=1):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        # Only consider local relative paths for fixing
        if src.startswith(("http://", "https://", "//")):
            continue
        p = (slug_dir / src).resolve()
        if p.exists():
            continue
        # Try existing asset files
        replacement = None
        try:
            for f in sorted(assets_dir.glob("*")):
                if f.is_file():
                    replacement = f
                    break
        except Exception:
            replacement = None
        if replacement is None:
            # Generate placeholder via local image tool
            alt_text = (img.get("alt") or "placeholder image")
            out_name = f"auto-img-{idx}.png"
            out_path_rel = f"pages/{slug}/assets/{out_name}"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0), follow_redirects=True) as client:
                    payload = {"prompt": alt_text, "page_slug": slug, "size": "512x512", "output_filename": out_path_rel}
                    await client.post(os.getenv("BASE_URL", "http://localhost:8000") + "/api/tools/image", json=payload)
                replacement = assets_dir / out_name
            except Exception:
                replacement = None
        if replacement and replacement.exists():
            img["src"] = os.path.relpath(replacement, slug_dir).replace(os.sep, "/")
            fix_count += 1
            changed = True

    if changed:
        try:
            html_path.write_text(str(soup), encoding="utf-8")
            result["changed"] = True
            result["fixed_images"] = fix_count
        except Exception:
            pass
    return result


async def assert_single_file_no_external(slug: str) -> Dict[str, Any]:
    """Verify there are no external <link rel="stylesheet"> or <script src> and all images resolve."""
    base_dir = Path(__file__).resolve().parent.parent
    slug_dir = base_dir / "pages" / slug
    html_path = slug_dir / "index.html"
    try:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    except Exception:
        return {"ok": False, "error": "unable_to_read"}
    externals: List[str] = []
    for link in soup.find_all("link"):
        rel = (link.get("rel") or [""])
        href = (link.get("href") or "").strip()
        if any((r or "").lower() == "stylesheet" for r in rel) and href.startswith(("http://", "https://", "//")):
            externals.append(href)
    for script in soup.find_all("script"):
        src = (script.get("src") or "").strip()
        if src and src.startswith(("http://", "https://", "//")):
            externals.append(src)
    img_broken = []
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith(("http://", "https://", "//")):
            continue
        p = (slug_dir / src).resolve()
        if not p.exists():
            img_broken.append(src)
    return {"ok": (not externals and not img_broken), "externals": externals, "broken_images": img_broken}


