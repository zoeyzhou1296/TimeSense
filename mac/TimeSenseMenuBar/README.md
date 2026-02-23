# TimeSenseMenuBar (native macOS menu-bar app)

This is a minimal native macOS menu-bar app (Swift/SwiftUI) that opens a tiny popover with a **WebView** pointing to your local TimeSense server (default: `http://127.0.0.1:8000/`).

## How to run

1) Open `TimeSenseMenuBar.xcodeproj` in Xcode  
2) Run the `TimeSenseMenuBar` target  
3) Ensure the backend is running:

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Notes
- This is MVP scaffolding: it provides the “always-available” entry point (menu bar).
- Next iteration:
  - global hotkey
  - a dedicated quick-capture UI (native buttons) instead of full web page
  - optional “mini mode” route on the backend for a cleaner embedded view


