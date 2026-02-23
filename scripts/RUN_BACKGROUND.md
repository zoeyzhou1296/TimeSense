# Run TimeSense in the background (no Cursor needed)

You can keep the TimeSense backend running without keeping Cursor or a terminal window open.

---

## Option A: Run in Terminal.app (simplest)

1. Open **Terminal** (macOS app: Spotlight → "Terminal").
2. Run:
   ```bash
   cd "/Users/zoeyzhou/Projects/AI Architect"
   source .venv/bin/activate
   uvicorn main:app --host 127.0.0.1 --port 8000
   ```
3. Minimize the Terminal window (or leave it on another desktop). You can close **Cursor**; the server keeps running as long as you don’t quit Terminal.

---

## Option B: LaunchAgent (runs at login, fully in background)

TimeSense runs like a system service: no terminal window, restarts if it crashes.

**What “at login” means:** When you **log in to your Mac**—e.g. you open the laptop and enter your password (or Touch ID), or after a restart. The backend starts automatically then. It does *not* mean when you open an app or a browser.

**Current setup:** The agent runs from **~/Projects/AI Architect**. To reinstall after moving the project, run from the project root: `./scripts/install-launchagent.sh`

### One-time setup

1. **Install the LaunchAgent** (copy the plist into your user LaunchAgents folder):
   ```bash
   mkdir -p ~/Library/LaunchAgents
   cp "/Users/zoeyzhou/Projects/AI Architect/scripts/com.zoeyzhou.timesense.backend.plist" ~/Library/LaunchAgents/
   ```

2. **Load and start**:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.zoeyzhou.timesense.backend.plist
   ```

3. **Check it’s running**: open http://127.0.0.1:8000 in your browser.

### Useful commands

- **Stop**: `launchctl unload ~/Library/LaunchAgents/com.zoeyzhou.timesense.backend.plist`
- **Start again**: `launchctl load ~/Library/LaunchAgents/com.zoeyzhou.timesense.backend.plist`
- **Logs**: `tail -f /tmp/timesense-backend.log` and `tail -f /tmp/timesense-backend.err`

With `RunAtLoad` and `KeepAlive` in the plist, the backend starts at login and restarts if it exits.

---

## Option C: Background in Terminal (one-off)

From any terminal (including Cursor’s), from the project directory:

```bash
cd "/Users/zoeyzhou/Projects/AI Architect"
nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/timesense.log 2>&1 &
```

The server keeps running after you close that terminal only if your shell is configured not to send SIGHUP to background jobs (e.g. `disown` or a dedicated terminal session). For a reliable “always on” setup, use **Option B** instead.

---

**Summary:** Use **Terminal.app** (Option A) to close Cursor and keep TS running; use **LaunchAgent** (Option B) for a fully ambient, start-at-login backend. Use the app at **http://127.0.0.1:8000** and pick **supermind-agent-v1** in the AI model dropdowns when you want that model.
