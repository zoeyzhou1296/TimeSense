# TimeSense (MVP)

Local MVP for low-friction time logging + gaps + reminders.

## Run locally

```bash
cd "/Users/zoeyzhou/Desktop/AI Architect"
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open: `http://127.0.0.1:8000/`

## macOS companion (menu-bar + EventKit sync)
There is a native macOS menu-bar companion app that reads Apple Calendar (EventKit)
and syncs planned events to `/api/apple_sync/planned_upsert`.

Source: `mac/TimeSenseMacCompanion/`

Quick steps (Xcode):
1) Create a macOS App project (`TimeSenseMacCompanion`, SwiftUI/Swift).
2) Drag files from `mac/TimeSenseMacCompanion/` into the project.
3) Ensure `NSCalendarsUsageDescription` is set in the target Info.
4) Run, then click the ⏱ menu-bar icon and press **Sync now**.

## Environment variables

### Required for Google connect (Calendar)
- `SESSION_SECRET`: random string for session cookies (OAuth state)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` (default: `http://127.0.0.1:8000/auth/google/callback`)

### Required for Outlook connect (Calendar via Microsoft Graph)
- `MS_CLIENT_ID`
- `MS_CLIENT_SECRET`
- `MS_REDIRECT_URI` (default: `http://127.0.0.1:8000/auth/microsoft/callback`)
- `MS_TENANT` (optional; default: `common`)

### Apple Calendar (EventKit) companion sync
The iPhone companion will call:

`POST /api/apple_sync/planned_upsert`

If `COMPANION_SYNC_TOKEN` is set in the server env, the companion must send it as:
- `Authorization: Bearer <token>` (preferred), or
- `X-TimeSense-Companion-Token: <token>`

Body shape (JSON):
```json
{
  "range_start": "2026-01-20T00:00:00Z",
  "range_end": "2026-01-27T00:00:00Z",
  "events": [
    {
      "external_id": "eventIdentifier-from-EventKit",
      "title": "Weekly sync",
      "start_at": "2026-01-21T17:00:00Z",
      "end_at": "2026-01-21T17:30:00Z",
      "is_all_day": false,
      "source_calendar_name": "Work Outlook"
    }
  ]
}
```

Server behavior: delete existing **apple_eventkit** events overlapping the range, then upsert incoming events.

### Companion token (optional)
- `COMPANION_SYNC_TOKEN`: if set, required for `/api/apple_sync/planned_upsert`.

### Optional for Web Push (PWA)
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT` (default: `mailto:admin@example.com`)

If VAPID keys are not set, the app still works; push endpoints will return an error like “VAPID keys not configured”.

## Current MVP capabilities
- Quick log (1 tap) using top prompt categories
- “Drift” tag checkbox
- Today timeline + daily review gap fill UI (very simple)
- Hourly + 23:00 jobs (push only; requires VAPID + installed PWA)
- Google connect button + token storage skeleton (Calendar sync/writeback comes next)


