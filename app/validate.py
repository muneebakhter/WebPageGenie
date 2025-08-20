from __future__ import annotations

from typing import Any, Dict, List


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


