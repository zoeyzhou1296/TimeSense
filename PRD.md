# TimeSense — PRD (Final v1.1)

Owner: Zoey (solo)  
Platforms: macOS + iPhone (iOS)  
Primary form factor (MVP): Web app + PWA (iPhone “Add to Home Screen”) + macOS menu-bar/keyboard quick capture  

---

## 1) Product goal

Build a low-friction time logging tool that:

- Captures time with **minimum shame + minimum effort**, especially “unplanned drifting” moments.
- Uses your history to detect patterns and provide **focus / efficiency insights**.
- Works across **Mac + iPhone**, with cloud sync and a public URL so anyone can sign up and use it.

---

## 2) Constraints & principles

### Constraints (hard)
- iPhone 13, limited storage → keep mobile client light; store data in the cloud.
- iOS Web/PWA **cannot access Apple Health (HealthKit)** → sleep import requires a future iOS native companion app.
- iOS passive “screen unlock count / per-app usage time” is limited for third-party apps; MVP should not depend on it.
- You do **not** want ActivityWatch.

### Principles (product)
- **Raw logs are preserved** (what you wrote stays). AI classification is a layer *on top*, mainly for insights.
- Mobile prompts must offer **3–5 one-tap choices + Custom**, never a long form.
- Avoid calendar pollution: Calendar is **a view/export**, not the canonical database.

---

## 3) Success metrics (OKRs)

### Completeness (recording becomes more complete)
- Daily coverage \(logged + confirmed gaps\) / awake time ≥ **90%**
- Daily “gap minutes” trend decreases week-over-week
- Backfill latency decreases (time from gap creation → confirmed classification)

### Focus / fragmentation
- Fewer <5-min entries (proxy for less fragmentation)
- More deep-focus blocks (≥45 min) per week
- Decrease in “Unplanned wasting” + “Drift” tag minutes (trend, not perfection)

### Insights efficacy
- Weekly insight review completion rate
- Suggestion adoption (you mark “try this”) and 2-week follow-up improvement signals

---

## 4) Canonical data model (single source of truth)

### Canonical store: Cloud DB
All data used for insights lives in the **cloud database** (canonical). Google Calendar writeback is optional and partial.

### Key definitions
- **Raw entry**: user-authored title/notes + times. Never overwritten.
- **AI classification**: a suggested category + tags produced from raw content; user can accept/edit.
- **Planned**: calendar events (Google/Outlook) imported as context.
- **Logged**: user-confirmed time entries (manual or backfill).
- **Gap**: uncovered time region, shown for backfilling.

---

## 5) Categories & tags

### Categories (initial)
- Work (active): coding / writing / thinking / creating
- Work (passive): meetings / coordination / admin / “social at work”
- Learning
- Exercise
- Intimacy / quality time
- Chores
- Social
- Commute
- Unplanned wasting
- Other

### Tags (orthogonal)
- Drift / Mindless browsing
- Frequent switching
- Deep work
- Avoidance (when you want to mark “hit difficulty → escaped”)

UI rule: category is required; tags optional.

---

## 6) Data collection (MVP)

### 6.1 iPhone (PWA)
- “Quick log” big buttons: the top 3–5 categories + Custom.
- Default time range: **from last known boundary to now** (1 tap completes).
- Optional: tag selection after category (one extra tap).

### 6.2 macOS (Phase 1, not Phase 1.5)
- Menu-bar app (or lightweight desktop shell) that opens a quick capture popover:
  - same 3–5 buttons + Custom
  - hotkey to open (configurable)
  - optionally “Start focus block” timer UI (lightweight)

### 6.3 Calendar import (context, not truth)
- Google Calendar import for planned blocks.
- Outlook calendar: see sync options below.

### 6.4 Apple Calendar (aggregated) planned sync — iPhone companion (Phase 2, recommended)
Context: Your work Outlook calendar is the most important source, and Microsoft app registration / Graph consent may be blocked by corporate policy. Meanwhile, your **Apple Calendar already aggregates** Google + Outlook on-device.

Solution: ship a lightweight **iPhone companion app** (read-only) using **EventKit** to sync planned events into TimeSense:
- Reads calendars visible in Apple Calendar (including **work Outlook**).
- Uploads normalized planned blocks to TimeSense cloud DB.
- TimeSense UI treats this as “Apple Calendar (aggregated)” planned source and shows the originating calendar name (e.g., “Work Outlook”).

Privacy scope (locked): **A — title only**
- Stored fields: `start_at`, `end_at`, `title`, `is_all_day`, `source_calendar_name`
- Not stored: location, notes, attendees

#### iPhone companion spec (minimal v1)
- **Permissions**: EventKit read-only (Calendars), Background App Refresh.
- **Sync strategy**:
  - On first run: fetch next 30 days + previous 7 days planned events.
  - Then: periodic background sync (every 30–60 min) + manual “Sync now”.
  - Upload by time window (range) to minimize payloads.
- **Sync payload**: list of events with stable `external_id` (EventKit eventIdentifier), `title`, `start_at`, `end_at`, `is_all_day`, `source_calendar_name`.
- **Server**: upsert by `(user_id, source, external_id)` and delete-replace by range.

#### Planned sync API (to implement)
- `POST /api/apple_sync/planned_upsert`
  - body: `range_start`, `range_end`, `events[]`
  - effect: delete existing Apple-synced events in that range, then upsert incoming events.

---

## 7) Reminders & backfill (your preferred policy)

### Hourly reminder (max once per hour)
Trigger: if the last 60 minutes contain gaps/unconfirmed time (and you didn’t already dismiss/complete this hour).

Prompt UI (iPhone + web):
- 3–5 one-tap choices (customizable)
- “Custom…”
- “Skip for now”

### Daily review reminder
- Fixed at **23:00** every day.
- Opens “Daily Review” view: gaps highlighted; batch-fill supported.

### Reminder delivery channels (iPhone MVP)
Goal: avoid annoying email while still giving you reliable nudges.

- Primary: **Web Push** to installed PWA (best UX).
- Fallback A: **in-app banner + badge** when you open the app (always available).
- Fallback B (recommended): **calendar reminder** (create a single daily 23:00 “Daily Review” reminder in a dedicated “TimeSense Reminders” Google calendar).
- Fallback C (last resort): email (default OFF; if ON, prefer daily-only, not hourly).

---

## 8) Calendar writeback strategy (recommended)

### Recommendation
Keep the **database as ground truth**. Write back only selected categories to avoid polluting your main calendar.

### How “original data” and “insights data” work
- **Original data** = raw entries in the cloud DB (always complete, includes unplanned wasting).
- **Insights** read from the cloud DB (not from Google Calendar).
- Google Calendar writeback is a *projection* to help you plan/visualize, not the storage.

### Writeback policy
- Write only categories you mark as “writable” (e.g., Work(active), Learning, Exercise).
- Write into a dedicated calendar: **“TimeSense Logs”**.
- Rounding: **15-minute** alignment (your preference).

### Colors
- For imported planned events: keep existing colors (read from Google Calendar metadata).
- For writeback events: reuse your existing category→color mapping if available; otherwise define a mapping in Settings.

---

## 9) Outlook / work calendar continuous sync (options)

Goal: bring Outlook events into TimeSense as planned context.

### Option A (best, if allowed): Microsoft Graph OAuth (recommended)
- Connect Microsoft account; sync calendar events via Graph API.
- Supports incremental sync and ongoing updates.

### Option B (fallback, read-only): ICS subscription
- Import via ICS feed periodically (limitations depend on tenant settings).
- Typically read-only and may not carry rich metadata.

### Option C (personal workaround): unify via Apple Calendar
- If your Apple Calendar already shows Outlook + Google merged, we can *still* prefer direct APIs for reliability.
- Apple Calendar itself isn’t a server API we can depend on from web.

MVP scope: implement Google Calendar first, add Outlook (Option A) next.

### Recommendation given corporate constraints
If Microsoft Graph OAuth is blocked:
- Short-term: rely on Google planned context + your logs
- Long-term (guaranteed Outlook inclusion): implement Section **6.4** (EventKit planned sync)

---

## 10) AI agent behavior (classification & insights)

### 10.1 Classification agent
Input:
- raw title/note (English + occasional Chinese)
- time-of-day, day-of-week
- planned context (what calendar event overlaps)
- your personal taxonomy (categories + tags)

Output:
- suggested category
- suggested tags
- confidence
- rationale (for transparency)

Rules:
- Never edit raw content.
- If confidence low, keep “Other” and ask you in review.

### 10.2 Insights / coach agent
Outputs:
- Daily dashboard insights (short)
- Weekly report:
  - patterns (drift hotspots, fragmentation trend, deep-focus trend)
  - 3 suggestions + 1 “experiment for next week”
  - 2–3 reflective questions

Coach UI:
- dashboard + chat window (“coach conversation”)

---

## 11) Pages / UX (MVP)

- Auth (Google sign-in)
- Timeline (day view with gaps)
- Quick capture (web + iPhone PWA)
- Daily Review (gap fill, batch actions)
- Dashboard (today + last 7 days)
- Coach (weekly report + chat)
- Settings (categories, top 3–5 prompt choices, tag list, reminder policy, calendar connections, color mapping)

### 11.1 Outlook connect test flow (UI/UX)

Placement: Settings → “Calendar connections” → “Outlook (work)” card.

#### Button states
- **Connect Outlook** (primary)
  - default label: `Connect Outlook`
  - on click: opens Microsoft OAuth popup / redirect
  - disabled while in-flight; label: `Connecting…`
- **Connected** (success state)
  - label: `Connected ✓`
  - subtext: `Last synced: <timestamp>`
  - secondary actions:
    - `Sync now`
    - `Disconnect`
- **Blocked (needs admin approval)** (error state)
  - label: `Needs admin approval`
  - primary action: `Try ICS instead`
  - secondary action: `Copy request for IT`
  - subtext: `Your organization requires an admin to approve calendar access.`
- **Failed (other error)** (error state)
  - label: `Retry`
  - subtext: `Couldn’t connect. Please try again.`

#### Error copy (exact)
- **Admin approval required (detected from OAuth error / page)**
  - Title: `Outlook connection needs admin approval`
  - Body:
    - `Your organization blocks self-service permission grants for calendar access.`
    - `Options:`
      - `1) Ask IT to approve access (recommended for continuous sync).`
      - `2) Use an ICS subscription (read-only) if your organization allows publishing calendar links.`
  - CTA buttons: `Try ICS instead` (primary), `Copy request for IT` (secondary), `Close`
- **Tenant blocks external apps**
  - Title: `Outlook connection blocked by policy`
  - Body:
    - `This Microsoft account can’t authorize third-party apps.`
    - `You can still use TimeSense with Google Calendar + your logs, and optionally add Outlook via ICS.`
  - CTA buttons: `Try ICS instead`, `Close`

#### “Copy request for IT” template
Copy to clipboard:
`Hi IT team — I’m trying to connect my Outlook calendar to TimeSense for read-only event sync. The Microsoft sign-in flow says admin approval is required. Could you approve calendar read permission for this app? If not possible, please confirm whether publishing an ICS subscription link is allowed. Thanks.`

#### ICS fallback flow (UI)
- Button: `Try ICS instead`
- Modal asks for: `ICS URL`
- Helper text:
  - `This is read-only and may not include all metadata. Sync frequency depends on your organization.`
- Save action: `Add ICS feed`

---

## 12) Tech (MVP)

- Frontend: PWA (mobile-first) + responsive desktop
- macOS menu-bar: **native macOS** (Swift/SwiftUI) menu-bar app + hotkey + quick capture popover
- Backend: FastAPI
- DB: Postgres (cloud) or SQLite for local dev
- Auth: Google OAuth
- Calendar: Google Calendar API (read planned + write to “TimeSense Logs”)
- Scheduling: server-side jobs for reminder eligibility + weekly report generation

---

## 13) Open questions (only remaining blockers)

1) For mac menu-bar: **Decision: native macOS** (Swift/SwiftUI menu-bar app) in Phase 1.
2) For reminders on iOS: **Decision: try Web Push first**, avoid email by default.
   - Primary: Web Push for installed PWA (requires Add-to-Home-Screen + notification permission)
   - Fallback A: in-app reminders when you open the app (shows “you have gaps” banner)
   - Fallback B (optional): email only if push is not available or user opts in
3) For Outlook: user may be on a managed tenant. We will support:
   - Primary: Microsoft Graph OAuth connect (best)
   - Fallback: ICS subscription import (read-only) or manual upload

### How to test if Outlook is blocked (admin consent required)
We don’t need to guess. The product will provide a “Connect Outlook” button:
- If the org allows user consent: the OAuth flow completes and we start syncing.
- If the org blocks it: Microsoft will show “Need admin approval” / consent error. We store the failure and prompt you to use ICS/manual import until admin approves.


