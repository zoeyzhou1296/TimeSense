# TimeSenseMacCompanion (native macOS menu-bar + EventKit sync)

This is a native macOS menu-bar companion app that:
- Reads Apple Calendar (EventKit) to capture planned events (including Outlook).
- Syncs planned events to the TimeSense backend via `/api/apple_sync/planned_upsert`.
- Shows a tiny status/settings popover from the menu bar.

## Setup (Xcode)
1) Open Xcode → File → New → Project → macOS → App.
2) Product Name: `TimeSenseMacCompanion`, Interface: SwiftUI, Language: Swift.
3) Drag all files from this folder into the project (Copy items if needed).
4) In target **Info** tab, ensure `NSCalendarsUsageDescription` is set:
   - `TimeSense reads calendar titles to sync planned events.`
   - Or set the target to use the provided `Info.plist`.
5) Run the app.

## Usage
- Click the ⏱ menu bar icon.
- **Base URL**: Use the same address you use to open TimeSense in the browser, e.g. `http://127.0.0.1:8000` or `http://localhost:8000`. If this is wrong or empty, you’ll see “Invalid server URL.” and “Events Synced: 0”.
- Click **Sync now**. Then in the TimeSense web app, click the ↻ refresh button to see the synced events.

## Backend
Ensure the backend is running:

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Optional auth token
If `COMPANION_SYNC_TOKEN` is set on the server, enter the same token in the app.

