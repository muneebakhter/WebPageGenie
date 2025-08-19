#!/usr/bin/env bash
set -euo pipefail
export $(grep -v '^#' .env | xargs -d '\n') || true
uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --reload
