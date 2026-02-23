# TimeSense – Running, backup & deploy

## Keeping the app running and backing up

### 1. Backend (website API + static files)

The backend is the FastAPI app in `main.py`. It serves the web UI and stores data in `timesense.db`.

**Option A – Run in terminal (development)**

- Open a terminal in the project root (`/Users/zoeyzhou/Desktop/AI Architect`).
- Run:
  ```bash
  source .env   # or: export $(grep -v '^#' .env | xargs)
  uvicorn main:app --host 0.0.0.0 --port 8000
  ```
- **Yes, you need to keep this terminal open** while you use the site. Closing it stops the server.
- Visit: `http://localhost:8000` (or `http://<your-machine-ip>:8000` from other devices).

**Option B – Run in background (still need terminal)**

- Same as above but run in background:
  ```bash
  nohup uvicorn main:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &
  ```
- To stop later: `pkill -f "uvicorn main:app"`.

**Option C – Production (e.g. systemd or cloud)**

- Use a process manager (e.g. systemd on Linux) or a cloud app host (e.g. Railway, Render, Fly.io) so the server restarts on crash and survives reboots.
- Set env vars (e.g. `SESSION_SECRET`, `GOOGLE_*`, `AI_BUILDER_TOKEN`, etc.) in that environment.
- Point the app at the same `timesense.db` path (or a persistent volume) so data is kept.

**AI features (AI Builders Space API)**

- **Detailed Insights** in the app work in two ways:
  - **Without an API key:** The app uses built-in rules only (e.g. “sleep &lt; 6h”, “no learning this week”). These are superficial and do not use AI.
  - **With an API key:** If you set `AI_BUILDER_TOKEN`, the app calls the AI Builders Space API (GPT-5) to generate **insights from your actual time log** (your entries and categories). You get more specific, actionable suggestions based on what you really did.
- **Where is the `.env` file?**  
  In the **project root** (the folder that contains `main.py`). For example:  
  `.../Desktop/AI Architect/.env`  
  If you don’t see `.env` in Finder, use **Cmd+Shift+.** to show hidden files, or create a new file named `.env` in that folder.

- **How to add the token and connection:**
  1. Get an API token from [AI Builders Space](https://space.ai-builders.com) (or your admin).
  2. In the project root, create or edit `.env` and add:
     ```bash
     AI_BUILDER_TOKEN=your_token_here
     ```
  3. Optional: set the API base URL if different:
     ```bash
     AI_BUILDER_BASE_URL=https://space.ai-builders.com/backend/v1
     ```
  4. Restart the backend (e.g. restart uvicorn). No need to change anything in the browser.
- **Model:** The app uses **GPT-5** by default. To use a newer model (e.g. **gpt-5.2**) if your API supports it, add to `.env`:
  - `AI_BUILDER_ANALYSIS_MODEL=gpt-5.2` (for insights and “Analyze day”)
  - `AI_BUILDER_CATEGORIZE_MODEL=gpt-5.2` (for smart categorization)
  Then restart the backend.

- With `AI_BUILDER_TOKEN` set, these features use the API:
  - **Smart categorization** (e.g. “called Mom” → Intimacy / Quality Time) – uses GPT-5 (or the model above) when token is present.
  - **Detailed Insights** (week and range) – AI suggests 3–5 actionable tips from your log.
  - **Day note “Analyze day”** – AI reflection combining your note and that day’s activity.
  - **AI categorize** checkbox in Quick Log – uses AI for the “What did you do?” field.

- If AI features still don’t work after adding the token, **restart the backend** (stop and start `uvicorn`). The server reads `.env` only at startup. Then try again; the app will show a short error (e.g. “AI API error 401: …”) so you can see what’s wrong.

**Backup**

- The only persistent data is the SQLite file: `timesense.db`.
- Back up by copying it regularly, e.g.:
  ```bash
  cp timesense.db "timesense.backup.$(date +%Y%m%d).db"
  ```
- Optional: sync the backup to cloud storage (iCloud, Dropbox, etc.).

---

### 2. Mac Companion (Xcode app for Apple Calendar sync)

- Open the Xcode project:  
  `mac/TimeSenseMacCompanionXcode/TimeSenseMacCompanion/TimeSenseMacCompanion.xcodeproj`
- Set the backend URL and token in the app (or in Settings in the app).
- Run the app from Xcode (▶) or build and run the built app.
- **To keep syncing and “backing up” (pushing events to the server):**
  - Either keep the Mac app running in the background (menu bar or dock), **or**
  - Run it when you want a sync (e.g. once a day). The app syncs in chunks (e.g. every 6 hours if you left it running).
- You do **not** need to keep a terminal open for the Mac app; only the backend (above) must be running for the website and for the Mac app to push events.

**Summary**

| What you want              | What to keep open / do                          |
|----------------------------|--------------------------------------------------|
| Use the website            | Backend running (terminal with uvicorn or nohup) |
| Website + backup DB        | Same as above; periodically copy `timesense.db` |
| Apple Calendar → server    | Mac Companion app running (Xcode or built app)  |
| No terminal for backend    | Deploy backend with systemd/cloud (see above)   |

---


### 4. Deploy at a “website” (e.g. always-on URL)

- **If you only need it on your home network:** Run the backend with `--host 0.0.0.0` and use your Mac’s local IP (e.g. `http://192.168.1.x:8000`) from other devices. No need to keep a browser open; just keep the terminal (or nohup) running as above.
- **If you want a public URL:** Deploy the same FastAPI app to a host that gives you a URL (e.g. Railway, Render, Fly.io, or a VPS with nginx + uvicorn/gunicorn). Put `timesense.db` on a persistent volume and set all env vars there. No need to keep your own terminal open; the host keeps the process running.

---

### 4. Sleep schedule “on top”

- Sleep that spans midnight (e.g. 23:00–09:00) is now split by the API so it appears on **both** days.
- On “today” you’ll see the morning segment (e.g. 00:00–09:00) at the **top** of that day’s column. Sleep segments are also ordered so Sleep appears first when overlapping with other events.
