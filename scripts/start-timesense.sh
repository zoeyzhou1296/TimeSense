#!/bin/bash
ROOT="/Users/zoeyzhou/Projects/AI Architect"
cd "$ROOT" || exit 1
export PATH="$ROOT/.venv/bin:$PATH"
exec uvicorn main:app --host 127.0.0.1 --port 8000
