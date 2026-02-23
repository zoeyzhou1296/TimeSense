# TimeSense iPhone Companion (Native) — Setup

This folder contains the SwiftUI source for a minimal EventKit sync app.
Create an Xcode iOS app project and drop these files in.

## Steps (Xcode)
1) Xcode → File → New → Project → iOS → App.
2) Product Name: `TimeSenseCompanion`
3) Interface: SwiftUI, Language: Swift.
4) Drag all `.swift` files in this folder into the project.
5) In Info.plist, add:
   - `NSCalendarsUsageDescription` = `TimeSense reads calendar titles to sync planned events.`
6) Run on a real device (EventKit requires device permission).

## Testing on iPhone (local dev)
1) Ensure your Mac server is running: `uvicorn main:app ...`
2) Find your Mac LAN IP (e.g., `http://192.168.1.23:8000`) and set it in the app.
3) Tap `Sync now`.

## Optional token
If `COMPANION_SYNC_TOKEN` is set on the server, enter the same token in the app.

