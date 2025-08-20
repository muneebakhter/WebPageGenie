from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import json
import urllib.request
import asyncio
import httpx
from dotenv import load_dotenv, find_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PAGES_DIR = BASE_DIR / "pages"
STATIC_DIR = BASE_DIR / "app" / "static"


def _ensure_asset_dir(slug: Optional[str]) -> Path:
    if slug:
        out_dir = PAGES_DIR / slug / "assets"
    else:
        out_dir = PAGES_DIR / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


async def _adownload_to(url: str, dest_path: Path, timeout: int = 30) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(r.content)


def _write_placeholder_svg(prompt: str, dest_path: Path) -> None:
    safe = (prompt or "").strip()[:200]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#4f46e5"/>
      <stop offset="100%" stop-color="#06b6d4"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" fill="url(#g)"/>
  <g transform="translate(512,480)">
    <circle r="220" fill="rgba(255,255,255,0.15)"/>
    <circle r="340" stroke="rgba(255,255,255,0.18)" stroke-width="10" fill="none"/>
  </g>
  <text x="512" y="760" font-size="36" font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial" text-anchor="middle" fill="#ffffff" opacity="0.9">{safe}</text>
  <text x="24" y="50" font-size="20" font-family="monospace" fill="#ffffff" opacity="0.7">placeholder · no API key</text>
  <text x="24" y="78" font-size="16" font-family="monospace" fill="#ffffff" opacity="0.6">set REPLICATE_API_TOKEN to enable FLUX</text>
</svg>'''
    dest_path.write_text(svg, encoding="utf-8")


def _replicate_http_predict(token: str, model: str, inputs: Dict[str, Any], timeout_s: int = 60, debug: bool = False) -> Tuple[Optional[str], Dict[str, Any]]:
    """Create a Replicate prediction via HTTP and poll until completion.
    Returns (first_output_url_or_none, debug_info_dict).
    """
    owner, name = model.split("/")
    create_url = f"https://api.replicate.com/v1/models/{owner}/{name}/predictions"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps({"input": inputs}).encode("utf-8")
    req = urllib.request.Request(create_url, data=body, headers=headers, method="POST")
    debug_info: Dict[str, Any] = {}
    if debug:
        masked = headers.copy()
        if "Authorization" in masked:
            masked["Authorization"] = "Token ***MASKED***"
        debug_info["create_request"] = {
            "url": create_url,
            "headers": masked,
            "body": json.loads(body.decode("utf-8")),
        }
    with urllib.request.urlopen(req, timeout=30) as resp:
        create_json = json.loads(resp.read().decode("utf-8"))
    if debug:
        debug_info["create_response"] = create_json
    prediction_id = create_json.get("id")
    if not prediction_id:
        return None, debug_info
    # Poll
    import time as _t
    get_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
    t0 = _t.time()
    while True:
        req_get = urllib.request.Request(get_url, headers=headers, method="GET")
        with urllib.request.urlopen(req_get, timeout=30) as r2:
            status_json = json.loads(r2.read().decode("utf-8"))
        if debug:
            debug_info.setdefault("polls", []).append(status_json)
        status = status_json.get("status")
        if status in ("succeeded", "failed", "canceled"):
            break
        if _t.time() - t0 > timeout_s:
            break
        _t.sleep(1)
    if status_json.get("status") == "succeeded":
        out = status_json.get("output") or []
        if isinstance(out, list) and out:
            return str(out[0]), debug_info
        if isinstance(out, str):
            return out, debug_info
    return None, debug_info


async def _bfl_http_predict_async(token: str, prompt: str, aspect_ratio: str = "1:1", timeout_s: int = 180, debug: bool = False) -> Tuple[Optional[str], Dict[str, Any]]:
    """Call BFL FLUX endpoint and poll until completion. Returns (url_or_none, debug)."""
    import time as _t
    create_url = "https://api.bfl.ai/v1/flux-kontext-pro"
    headers = {
        "accept": "application/json",
        "x-key": token,
        "Content-Type": "application/json",
    }
    payload = {"prompt": prompt, "aspect_ratio": aspect_ratio}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(create_url, data=body, headers=headers, method="POST")
    debug_info: Dict[str, Any] = {}
    if debug:
        masked = headers.copy(); masked["x-key"] = "***MASKED***"
        debug_info["create_request"] = {"url": create_url, "headers": masked, "body": payload}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(create_url, headers=headers, json=payload)
        resp.raise_for_status()
        create_json = resp.json()
    if debug:
        debug_info["create_response"] = create_json
    # Some BFL responses return final result immediately
    try:
        immediate = create_json.get("result", {})
        if isinstance(immediate, dict) and isinstance(immediate.get("sample"), str):
            return str(immediate["sample"]), debug_info
    except Exception:
        pass
    polling_url = create_json.get("polling_url") or create_json.get("status_url")
    if not polling_url:
        return None, debug_info
    t0 = _t.time()
    url_out: Optional[str] = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r2 = await client.get(polling_url, headers={"accept": "application/json", "x-key": token})
            r2.raise_for_status()
            status_json = r2.json()
            if debug:
                debug_info.setdefault("polls", []).append(status_json)
            # Extract possible output URL from various shapes
            def pick_url(obj: Any) -> Optional[str]:
                if isinstance(obj, str):
                    return obj
                if isinstance(obj, dict):
                    # BFL commonly returns result.sample
                    if isinstance(obj.get("sample"), str):
                        return obj["sample"]
                    for k in ("image_url", "url"):
                        if isinstance(obj.get(k), str):
                            return obj[k]
                if isinstance(obj, list) and obj:
                    return pick_url(obj[0])
                return None
            # Look into result (preferred), then other containers
            for key in ("result", "output", "image", "images", "data"):
                url_out = pick_url(status_json.get(key)) or url_out
            status = (status_json.get("status") or status_json.get("state") or "").lower()
            if url_out and status in ("succeeded", "success", "completed", "done"):
                break
            if status in ("failed", "error", "canceled"):
                break
            if _t.time() - t0 > timeout_s:
                break
            await asyncio.sleep(1)
    return url_out, debug_info


async def generate_image_file_async(
    prompt: str,
    page_slug: Optional[str] = None,
    size: str = "1024x1024",
    seed: Optional[int] = None,
    debug: bool = False,
    output_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate an image using FLUX via Replicate if REPLICATE_API_TOKEN is set,
    otherwise write a local SVG placeholder. Returns info dict.
    """
    out_dir = _ensure_asset_dir(page_slug)
    ts = time.strftime("%Y%m%dT%H%M%S")

    # Resolve desired destination path if requested
    desired_dest: Optional[Path] = None
    if output_filename:
        # Must be .png
        if not output_filename.lower().endswith(".png"):
            return {"error": "output_filename must end with .png", "saved": False}
        candidate = Path(output_filename)
        if not candidate.is_absolute():
            # Treat relative to project root
            candidate = (BASE_DIR / candidate).resolve()
        # Security: only allow saving under STATIC_DIR or PAGES_DIR
        try:
            c_str = str(candidate)
            if not (str(candidate).startswith(str(STATIC_DIR)) or str(candidate).startswith(str(PAGES_DIR))):
                return {"error": "output path must be under app/static or pages/", "saved": False}
        except Exception:
            return {"error": "invalid output path", "saved": False}
        candidate.parent.mkdir(parents=True, exist_ok=True)
        desired_dest = candidate

    # Ensure latest .env is loaded (supports updating token without server restart)
    try:
        load_dotenv(find_dotenv(), override=True)
    except Exception:
        pass
    bfl_token = os.getenv("BFL_API_KEY") or os.getenv("BFL_AI_KEY")
    token = os.getenv("REPLICATE_API_TOKEN")
    if bfl_token:
        try:
            # Map size to aspect ratio
            width, height = 1024, 1024
            try:
                w, h = size.lower().split("x"); width, height = int(w), int(h)
            except Exception:
                pass
            if width == height:
                ar = "1:1"
            elif width >= height:
                ar = "16:9"
            else:
                ar = "9:16"
            url0, debug_info = await _bfl_http_predict_async(bfl_token, prompt, aspect_ratio=ar, timeout_s=180, debug=debug)
            if not url0:
                filename = f"img-{ts}.svg"; dest = out_dir / filename
                _write_placeholder_svg(prompt, dest)
                rel = f"/pages/{page_slug}/assets/{filename}" if page_slug else f"/pages/assets/{filename}"
                return {"provider": "bfl", "saved": True, "url": rel, "path": str(dest), "debug": debug_info if debug else None, "error": "bfl_request_failed"}
            if desired_dest is not None:
                dest = desired_dest
            else:
                ext = ".png"; filename = f"img-{ts}{ext}"; dest = out_dir / filename
            await _adownload_to(url0, dest)
            # Compute URL based on where it was saved
            if str(dest).startswith(str(STATIC_DIR)):
                rel = "/static/" + dest.relative_to(STATIC_DIR).as_posix()
            elif str(dest).startswith(str(PAGES_DIR)):
                rel = "/" + dest.relative_to(BASE_DIR).as_posix()
            else:
                rel = dest.as_posix()
            return {"provider": "bfl", "saved": True, "url": rel, "path": str(dest), "debug": debug_info if debug else None}
        except Exception as e:
            filename = f"img-{ts}.svg"; dest = out_dir / filename
            _write_placeholder_svg(prompt, dest)
            rel = f"/pages/{page_slug}/assets/{filename}" if page_slug else f"/pages/assets/{filename}"
            return {"provider": "placeholder", "saved": True, "url": rel, "path": str(dest), "error": str(e)}

    if token:
        try:
            width, height = 1024, 1024
            try:
                w, h = size.lower().split("x")
                width, height = int(w), int(h)
            except Exception:
                pass
            model = "black-forest-labs/flux-1-dev"
            # Minimal inputs for broad FLUX compatibility
            inputs: Dict[str, Any] = {"prompt": prompt, "width": width, "height": height}
            if seed is not None:
                inputs["seed"] = int(seed)

            # Keep replicate path sync underneath for now
            loop = asyncio.get_running_loop()
            url0, debug_info = await loop.run_in_executor(None, lambda: _replicate_http_predict(token, model, inputs, timeout_s=90, debug=debug))
            if not url0:
                # Fallback to placeholder
                filename = f"img-{ts}.svg"
                dest = out_dir / filename
                _write_placeholder_svg(prompt, dest)
                rel = f"/pages/{page_slug}/assets/{filename}" if page_slug else f"/pages/assets/{filename}"
                return {
                    "provider": "placeholder",
                    "saved": True,
                    "url": rel,
                    "path": str(dest),
                    "debug": debug_info if debug else None,
                    "error": "replicate_request_failed",
                }
            if desired_dest is not None:
                dest = desired_dest
            else:
                ext = ".png"; filename = f"img-{ts}{ext}"; dest = out_dir / filename
            await _adownload_to(url0, dest)
            if str(dest).startswith(str(STATIC_DIR)):
                rel = "/static/" + dest.relative_to(STATIC_DIR).as_posix()
            elif str(dest).startswith(str(PAGES_DIR)):
                rel = "/" + dest.relative_to(BASE_DIR).as_posix()
            else:
                rel = dest.as_posix()
            return {
                "provider": "replicate",
                "model": model,
                "saved": True,
                "url": rel,
                "path": str(dest),
                "debug": debug_info if debug else None,
            }
        except Exception as e:
            # Fallback to placeholder on any failure
            filename = f"img-{ts}.svg"
            dest = out_dir / filename
            _write_placeholder_svg(prompt, dest)
            rel = f"/pages/{page_slug}/assets/{filename}" if page_slug else f"/pages/assets/{filename}"
            return {
                "provider": "placeholder",
                "saved": True,
                "url": rel,
                "path": str(dest),
                "error": str(e),
            }

    # No token: placeholder
    filename = f"img-{ts}.svg"
    dest = out_dir / filename
    _write_placeholder_svg(prompt, dest)
    rel = f"/pages/{page_slug}/assets/{filename}" if page_slug else f"/pages/assets/{filename}"
    return {
        "provider": "placeholder",
        "saved": True,
        "url": rel,
        "path": str(dest),
        "reason": "REPLICATE_API_TOKEN not set",
    }


