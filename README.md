# TimeSense (MVP)

**TimeSense** is a local-first time logging app: quick one-tap logs, calendar sync (Google, Apple, Outlook, ICS), daily review, and optional reminders. It keeps a SQLite database on your machine and can optionally write time blocks to a dedicated **TimeSense Logs** Google Calendar.

This README walks you through getting the app running from a fresh clone, then connecting Google Calendar and using the Mac/iPhone companions.

---

## Getting started (from clone)

Follow these steps from scratch. You need **Python 3.11+** and **git**.

### 1. Clone the repository

```bash
git clone https://github.com/zoeyzhou1296/TimeSense.git
cd TimeSense
```

The **project root** is this `TimeSense` folder (it contains `main.py`, `requirements.txt`, and `static/`). All commands below assume you are in the project root.

### 2. Create a virtual environment and install dependencies

Using a venv keeps TimeSense’s dependencies separate from your system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Leave the venv activated for the next steps.

### 3. Create a `.env` file

TimeSense reads environment variables from a file named **`.env`** in the project root. Create it there (same folder as `main.py`).

**Minimum to run the app (without Google):**

```bash
# Optional but recommended for sessions; required if you connect Google later
SESSION_SECRET=your-random-secret-at-least-32-chars
```

You can use any long random string for `SESSION_SECRET` (e.g. `openssl rand -hex 32`).

To **connect Google Calendar**, you’ll also add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` later (see [Connect Google Calendar](#connect-google-calendar)).

### 4. Run the server

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Then open in your browser: **http://127.0.0.1:8000/**

To allow access from other devices on your network (e.g. iPhone or Mac companion), run instead:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

and use `http://<your-computer-ip>:8000` from those devices.

---

## Setup (reference)

### Environment variables (`.env`)

Create a `.env` file in the **project root** (same folder as `main.py`). The app loads it at startup.

| Variable | Required for | Description |
|----------|---------------|-------------|
| `SESSION_SECRET` | Google (and Microsoft) OAuth | Random string for session cookies (OAuth state). **Required** if you use “Connect Google Calendar”. |
| `GOOGLE_CLIENT_ID` | Google Calendar | From Google Cloud Console (OAuth 2.0 Client ID). |
| `GOOGLE_CLIENT_SECRET` | Google Calendar | From Google Cloud Console. |
| `GOOGLE_REDIRECT_URI` | Google Calendar | Optional. Default: `http://127.0.0.1:8000/auth/google/callback`. For production, set to your deployed callback URL. |
| `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_REDIRECT_URI` | Outlook calendar | For Microsoft Graph (optional). |
| `COMPANION_SYNC_TOKEN` | Mac/iPhone companion | Optional. If set, companion must send this as `Authorization: Bearer <token>` to `/api/apple_sync/planned_upsert`. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Web Push (PWA) | Optional. For push notifications. |
| `AI_BUILDER_TOKEN` | AI insights | Optional. For AI Builders Space API (insights, smart categorization). |

Example minimal `.env` for local dev with Google:

```bash
SESSION_SECRET=your-random-secret-at-least-32-chars
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
```

---

## Connect Google Calendar

TimeSense can read your **primary** Google Calendar for planned events and write time logs to a dedicated **“TimeSense Logs”** calendar.

### Step 1: Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select one) and enable **Google Calendar API**:  
   APIs & Services → Library → search “Google Calendar API” → Enable.
3. **OAuth consent screen:**  
   APIs & Services → OAuth consent screen → External (or Internal for workspace) → fill App name, support email, developer contact. Add scopes later or leave default; TimeSense will request calendar scopes when you connect.
4. **Create OAuth 2.0 credentials:**  
   APIs & Services → Credentials → Create credentials → **OAuth client ID** → Application type: **Web application**.  
   - **Authorized redirect URIs:** add exactly:
     - `http://127.0.0.1:8000/auth/google/callback` (local)
     - If you deploy: `https://your-domain.com/auth/google/callback`
   - Copy the **Client ID** and **Client secret** into your `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

### Step 2: Set `.env` and restart

- Set `SESSION_SECRET`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` (see above).
- Restart the backend (e.g. stop and run `uvicorn` again). The server only reads `.env` at startup.

### Step 3: Connect in the app

1. Open the app → **Settings** (gear icon).
2. Click **Connect Google Calendar**.
3. Sign in with Google and grant **calendar read** and **calendar write** (for creating “TimeSense Logs” and writing entries).
4. You’ll be redirected back; the button will show **Connected**.

### Step 4: Use in the calendar view

- In the main view, turn on **“Include Google Calendar”** if you want planned events from Google to appear (alongside Apple Calendar / ICS). Turn it off if you already sync Google into Apple Calendar to avoid duplicates.
- TimeSense will create a calendar named **“TimeSense Logs”** in your Google account and write logged time there (optional writeback).

### If something goes wrong

- **“Google OAuth not configured”**  
  Backend doesn’t see `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. Check `.env` is in the project root and restart the server.

- **“Invalid OAuth state” or redirect shows an error**  
  You need `SESSION_SECRET` set so the session can store OAuth state. Set it and restart.

- **Redirect URI mismatch**  
  The URL in the error must match **exactly** what you added in Google Cloud (e.g. `http://127.0.0.1:8000/auth/google/callback` — no trailing slash, same scheme and port).

- **“Google connected, but missing permission to create calendars”**  
  You connected before the app requested full calendar scope. In Settings, **disconnect Google**, then **Connect Google Calendar** again and accept all requested permissions (including create/manage calendars).

---

## macOS companion (menu bar + EventKit sync)

A native macOS menu-bar app reads **Apple Calendar** (EventKit) and syncs planned events to the backend.

- **Source:** `mac/TimeSenseMacCompanionXcode/`
- **Docs:** See `mac/TimeSenseMacCompanionXcode/README.md` for Xcode setup (create macOS App, add `NSCalendarsUsageDescription`, entitlements).

**Quick steps:**

1. Open the Xcode project, build and run.
2. In the menu-bar app, set **Base URL** to your backend (e.g. `http://127.0.0.1:8000` or `http://<your-ip>:8000`).
3. Click **Sync now**. Refresh the web app to see synced events.

---

## iOS companion (EventKit sync)

- **Source:** `ios/TimeSenseCompanion/`
- **Docs:** See `ios/TimeSenseCompanion/README.md`. Run on a **real device**; grant calendar access when prompted.

---

## Bugs and troubleshooting

### General

- **“.env not loading”**  
  `.env` must be in the **project root** (same directory as `main.py`). Restart the server after editing. Use **Cmd+Shift+.** (Mac) in Finder to show hidden files.

- **OAuth / “Invalid state”**  
  Set `SESSION_SECRET` in `.env` and restart. Session middleware needs it to store the OAuth state.

- **Google “redirect_uri_mismatch”**  
  In Google Cloud Console, add the **exact** redirect URI (e.g. `http://127.0.0.1:8000/auth/google/callback`). No trailing slash; use `http` for local.

### Mac companion (EventKit / calendar)

- **Calendar permission denied or not asked**  
  - In Xcode, set **Info** → `NSCalendarsUsageDescription` (e.g. “TimeSense reads calendar titles to sync planned events.”).  
  - If you previously denied access: **System Settings → Privacy & Security → Calendar** and allow your app (or remove it and run again to get the prompt).

- **App Sandbox and calendar**  
  The companion uses `com.apple.security.personal-information.calendars` and `com.apple.security.network.client` in its entitlements. If you change the project, keep these so calendar and network still work.

- **“Invalid server URL” / “Events Synced: 0”**  
  Set **Base URL** in the companion to the same address you use in the browser (e.g. `http://127.0.0.1:8000`). If the backend is bound to `0.0.0.0`, you can use your Mac’s LAN IP for other devices.

### iOS companion

- **Calendar permission**  
  EventKit requires a real device. On first run, allow calendar access when the system prompts.

### Deployment / CI

- **Build fails: “No url found for submodule …”**  
  The repo had a git submodule (e.g. `mac/...`) without a URL in `.gitmodules`. Either remove the submodule and add those files as normal files, or add a proper `.gitmodules` entry. The Docker build clones the repo and may run submodule init; a broken submodule causes the build to fail.

---

## Optional: Outlook, Web Push, AI

- **Outlook (Microsoft Graph):** Set `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_REDIRECT_URI` (and optionally `MS_TENANT`). Add the redirect URI in Azure App registration. In the app, connect Microsoft in Settings.
- **Web Push (PWA):** Set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and optionally `VAPID_SUBJECT`. Without these, push endpoints return an error but the rest of the app works.
- **AI Builders (insights, categorization):** Set `AI_BUILDER_TOKEN` in `.env`. See DEPLOY.md for details.

---

## Current MVP capabilities

- Quick log (1 tap) with top prompt categories; “Drift” tag; today timeline and daily review gap fill.
- Calendar view: planned events from Apple Calendar (via companion), optional Google/Outlook, and imported ICS.
- Optional writeback of time logs to Google “TimeSense Logs” calendar.
- Hourly and 23:00 reminder jobs (Web Push; requires VAPID and installed PWA).
- AI insights and smart categorization when `AI_BUILDER_TOKEN` is set.

---

## API for companion sync

The backend exposes:

- **POST /api/apple_sync/planned_upsert**  
  Body: JSON with `range_start`, `range_end`, and `events` (each with `external_id`, `title`, `start_at`, `end_at`, `is_all_day`, `source_calendar_name`).  
  If `COMPANION_SYNC_TOKEN` is set, send it as `Authorization: Bearer <token>` or `X-TimeSense-Companion-Token: <token>`.

See the main README sections above for Google, Outlook, and env vars.
