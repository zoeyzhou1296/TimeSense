#!/usr/bin/env python3
"""Trigger deployment to AI Builders Space. Requires AI_BUILDER_TOKEN in .env."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    load_dotenv()
    token = os.getenv("AI_BUILDER_TOKEN")
    if not token:
        print("Error: AI_BUILDER_TOKEN not set. Add it to .env", file=sys.stderr)
        sys.exit(1)

    repo_url = "https://github.com/zoeyzhou1296/TimeSense"
    service_name = "timesense"
    branch = "main"

    body = {"repo_url": repo_url, "service_name": service_name, "branch": branch, "port": 8000}
    url = "https://space.ai-builders.com/backend/v1/deployments"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Deploy failed: HTTP {e.code}", file=sys.stderr)
        try:
            print(e.read().decode(), file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"Deploy failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Deployment queued.")
    print("URL: https://timesense.ai-builders.space/")
    print("Wait 5–10 minutes, then open the URL.")
    if data.get("streaming_logs"):
        print("\nBuild logs:")
        print(data["streaming_logs"])

if __name__ == "__main__":
    main()
