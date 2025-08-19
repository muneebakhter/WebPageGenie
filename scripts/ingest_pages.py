import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")

from app.db import init_db, reset_db
from app.ingest import ingest_all_pages

if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    pages_dir = base / "pages"
    if os.getenv("RESET", "0") == "1":
        reset_db()
    else:
        init_db()
    ingest_all_pages(pages_dir)
    print("Ingested pages from:", pages_dir)
