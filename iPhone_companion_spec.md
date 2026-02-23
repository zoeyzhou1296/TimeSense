# TimeSense iPhone Companion (EventKit) — Spec v1

## Goal
Bring Outlook (and any other Apple Calendar sources) into TimeSense by reading
EventKit on-device and syncing planned events to the server. Read-only, title
only. The cloud DB remains canonical.

## Scope (locked)
- Data source: EventKit (Apple Calendar aggregate on iPhone).
- Privacy scope A: title only (no notes, location, attendees).
- Sync target: `POST /api/apple_sync/planned_upsert`.
- Read-only. No edits or writeback to calendars.

## Permissions
- Calendars (EventKit read-only).
- Background App Refresh.

## Screens (minimal v1)
1) **Welcome / Explain**
   - Message: "TimeSense reads your Apple Calendar (title only) to show planned
     events like Outlook."
   - CTA: `Continue`

2) **Calendar Access**
   - System permission prompt (EKEventStore requestAccess).
   - States:
     - Allowed: proceed to Sync.
     - Denied: show instructions to enable in Settings.

3) **Sync Status (Home)**
   - Header: "Apple Calendar Sync"
   - `Last sync: <time>`
   - `Events synced: <count>`
   - Button: `Sync now`
   - Subtext: "Title only. No notes or attendees."

4) **Settings**
   - Server URL (default to production or localhost in dev).
   - Optional token field (matches `COMPANION_SYNC_TOKEN`).
   - Sync window: "Past 7 days, next 30 days" (read-only in v1).

## Sync schedule
- First run: past 7 days + next 30 days.
- Background sync: every 30–60 minutes when Background App Refresh is enabled.
- Manual: `Sync now` triggers immediate sync.

## Data selection and normalization
- Fetch events in a range with `EKEventStore.predicateForEvents`.
- Map fields:
  - `external_id`: `event.eventIdentifier`
  - `title`: `event.title ?? "Planned"`
  - `start_at`: event start date (ISO 8601 with timezone offset)
  - `end_at`: event end date (ISO 8601 with timezone offset)
  - `is_all_day`: `event.isAllDay`
  - `source_calendar_name`: `event.calendar.title`
- Time zone: use device locale; send ISO 8601 with offset (server converts to UTC).
- Deduping is handled server-side by `(user_id, source, external_id)` within the
  posted range.

## API contract
Endpoint: `POST /api/apple_sync/planned_upsert`

Headers:
- `Authorization: Bearer <token>` if `COMPANION_SYNC_TOKEN` is set on server.
- `Content-Type: application/json`

Body:
```json
{
  "range_start": "2026-01-20T00:00:00Z",
  "range_end": "2026-02-19T00:00:00Z",
  "events": [
    {
      "external_id": "eventIdentifier-from-EventKit",
      "title": "Weekly sync",
      "start_at": "2026-01-21T09:00:00-08:00",
      "end_at": "2026-01-21T09:30:00-08:00",
      "is_all_day": false,
      "source_calendar_name": "Work Outlook"
    }
  ]
}
```

Server behavior:
- Deletes existing `apple_eventkit` events overlapping the range.
- Upserts incoming events by `(user_id, source, external_id)`.

## Error states
- **Permission denied**: show "Calendar access is off. Enable in Settings."
- **Offline / no network**: show "Offline. Sync will retry."
- **401 Unauthorized**: show "Invalid sync token" (prompt to re-enter token).
- **5xx / timeouts**: show "Server error. Try again later."
- **Partial payload**: show "Some events failed; retry scheduled."

## Background tasks (iOS)
- Use `BGAppRefreshTask` with earliestBeginDate ~30 minutes.
- On task start: perform sync; reschedule next task.
- If background refresh is disabled, rely on manual sync.

## Success / debug signals
- Store `last_sync_at`, `last_sync_status`, `last_error`.
- Log upload counts (deleted/upserted) for UI display.

## Manual test
Use the mock script:
- `python scripts/mock_apple_sync.py --base-url http://127.0.0.1:8000 --token <token>`

