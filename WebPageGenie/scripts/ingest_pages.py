import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from app.db import init_db
from app.ingest import ingest_all_pages

if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    pages_dir = base / "pages"
    init_db()
    ingest_all_pages(pages_dir)
    print("Ingested pages from:", pages_dir)
