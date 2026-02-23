import argparse
import json
from datetime import datetime, timedelta, timezone

import requests


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def build_payload() -> dict:
    range_start = _utc_now().replace(hour=0, minute=0, second=0)
    range_end = range_start + timedelta(days=7)
    events = [
        {
            "external_id": "mock_event_1",
            "title": "Outlook: Weekly sync",
            "start_at": (range_start + timedelta(hours=9)).isoformat(),
            "end_at": (range_start + timedelta(hours=9, minutes=30)).isoformat(),
            "is_all_day": False,
            "source_calendar_name": "Work Outlook",
        },
        {
            "external_id": "mock_event_2",
            "title": "Focus block",
            "start_at": (range_start + timedelta(hours=14)).isoformat(),
            "end_at": (range_start + timedelta(hours=16)).isoformat(),
            "is_all_day": False,
            "source_calendar_name": "Personal",
        },
    ]
    return {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock EventKit sync payload")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()

    payload = build_payload()
    if args.print_only:
        print(json.dumps(payload, indent=2))
        return

    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    url = f"{args.base_url.rstrip('/')}/api/apple_sync/planned_upsert"
    resp = requests.post(url, headers=headers, json=payload, timeout=20)
    print(resp.status_code, resp.text)


if __name__ == "__main__":
    main()

