import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import requests
from icalendar import Calendar as ICalendar

APP_NAME = "TimeSense"
STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(__file__).parent / "timesense.db"

# Load local env (secrets live in .env; file itself is ignored)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)

# Sessions (used for OAuth state)
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

# Web Push (VAPID)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")

# Optional companion app token (EventKit sync)
COMPANION_SYNC_TOKEN = os.getenv("COMPANION_SYNC_TOKEN", "")

# Google OAuth (Calendar)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback")

GOOGLE_CAL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    # Needed to create/manage the dedicated "TimeSense Logs" calendar
    "https://www.googleapis.com/auth/calendar",
    # Read planned events
    "https://www.googleapis.com/auth/calendar.readonly",
    # Write back to the dedicated TimeSense Logs calendar
    "https://www.googleapis.com/auth/calendar.events",
]

# Microsoft OAuth (Outlook Calendar via Graph)
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
MS_REDIRECT_URI = os.getenv("MS_REDIRECT_URI", "http://127.0.0.1:8000/auth/microsoft/callback")
MS_TENANT = os.getenv("MS_TENANT", "common")  # common/organizations/consumers or tenant id

MS_AUTH_BASE = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0"
MS_SCOPES = ["offline_access", "Calendars.Read"]

# MVP mode: single-user local dev (Google OAuth + multi-user comes next)
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "zoey@example.com")
SINGLE_USER_ID = "user_local_zoey"

app = FastAPI(title=APP_NAME)
if SESSION_SECRET:
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL,
              timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              name TEXT NOT NULL,
              is_prompt_choice INTEGER NOT NULL DEFAULT 0,
              is_writable INTEGER NOT NULL DEFAULT 0,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS time_entries (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              start_at TEXT NOT NULL,
              end_at TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              category_id TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              source TEXT NOT NULL,
              device TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        # Migration: add planned_event_id so we can hide planned events that were categorized (no duplicate in UI/stats)
        cur = conn.execute("PRAGMA table_info(time_entries)")
        cols = [row[1] for row in cur.fetchall()]
        if "planned_event_id" not in cols:
            conn.execute("ALTER TABLE time_entries ADD COLUMN planned_event_id TEXT")
        # Migration: add color to categories (hex e.g. #7986cb; NULL = use default palette)
        cur = conn.execute("PRAGMA table_info(categories)")
        cat_cols = [row[1] for row in cur.fetchall()]
        if "color" not in cat_cols:
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT")
        _deduplicate_time_entries(conn)
        _delete_future_user_logs(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              p256dh TEXT NOT NULL,
              auth TEXT NOT NULL,
              user_agent TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_tokens (
              user_id TEXT PRIMARY KEY,
              access_token TEXT NOT NULL DEFAULT '',
              refresh_token TEXT NOT NULL DEFAULT '',
              token_uri TEXT NOT NULL DEFAULT '',
              client_id TEXT NOT NULL DEFAULT '',
              client_secret TEXT NOT NULL DEFAULT '',
              scopes_json TEXT NOT NULL DEFAULT '[]',
              expiry TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_calendar_state (
              user_id TEXT PRIMARY KEY,
              planned_calendar_id TEXT NOT NULL DEFAULT 'primary',
              logs_calendar_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ms_tokens (
              user_id TEXT PRIMARY KEY,
              access_token TEXT NOT NULL DEFAULT '',
              refresh_token TEXT NOT NULL DEFAULT '',
              expires_at TEXT NOT NULL DEFAULT '',
              scope TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS planned_events_imported (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              source TEXT NOT NULL,                 -- e.g. 'apple_ics'
              external_id TEXT NOT NULL,            -- stable per-source id
              source_calendar_name TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              is_all_day INTEGER NOT NULL DEFAULT 0,
              start_at TEXT NOT NULL,
              end_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(user_id, source, external_id)
            )
            """
        )
        conn.commit()

        conn.execute(
            """
            INSERT OR IGNORE INTO users (id, email, timezone, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (SINGLE_USER_ID, DEV_USER_EMAIL, "America/Los_Angeles", _utc_now().isoformat()),
        )
        conn.commit()

        cur = conn.execute("SELECT COUNT(1) FROM categories WHERE user_id = ?", (SINGLE_USER_ID,))
        count = int(cur.fetchone()[0])
        if count == 0:
            defaults = [
                ("Work (active)", 1, 1, 10),
                ("Work (passive)", 1, 0, 20),
                ("Learning", 1, 1, 30),
                ("Exercise", 1, 1, 40),
                ("Intimacy / quality time", 1, 0, 50),
                ("Chores", 0, 0, 60),
                ("Life essentials", 1, 0, 65),  # lunch, dinner, shower, cleaning, etc.
                ("Social", 0, 0, 70),
                ("Commute", 0, 0, 80),
                ("Unplanned wasting", 1, 0, 90),
                ("Other", 0, 0, 100),
            ]
            for name, is_prompt_choice, is_writable, sort_order in defaults:
                cid = f"cat_{secrets.token_hex(8)}"
                conn.execute(
                    """
                    INSERT INTO categories
                      (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        SINGLE_USER_ID,
                        name,
                        int(is_prompt_choice),
                        int(is_writable),
                        int(sort_order),
                        _utc_now().isoformat(),
                    ),
                )
            conn.commit()

        _ensure_category(conn, SINGLE_USER_ID, "Sleep", 0, 1, 110)
        _ensure_category(conn, SINGLE_USER_ID, "Life essentials", 1, 0, 65)
        conn.commit()


def _deduplicate_time_entries(conn: sqlite3.Connection) -> None:
    """Keep one per planned_event_id; for entries without it, keep one per exact (start_at, end_at, title) so duplicate logs are removed."""
    conn.row_factory = sqlite3.Row
    try:
        # 1) Same planned_event_id: keep oldest
        cur = conn.execute(
            """
            SELECT id, user_id, planned_event_id, created_at
            FROM time_entries
            WHERE planned_event_id IS NOT NULL AND planned_event_id != ''
            ORDER BY created_at ASC
            """
        )
        rows = cur.fetchall()
        keep_ids: set[str] = set()
        seen_key: set[tuple[str, str]] = set()
        for r in rows:
            key = (r["user_id"], r["planned_event_id"])
            if key not in seen_key:
                seen_key.add(key)
                keep_ids.add(r["id"])
        if rows and len(keep_ids) < len(rows):
            for r in rows:
                if r["id"] not in keep_ids:
                    conn.execute("DELETE FROM time_entries WHERE id = ?", (r["id"],))

        # 2) No planned_event_id: keep one per exact (user_id, start_at, end_at, title)
        cur2 = conn.execute(
            """
            SELECT id, user_id, start_at, end_at, title, created_at
            FROM time_entries
            WHERE planned_event_id IS NULL OR planned_event_id = ''
            ORDER BY created_at ASC
            """
        )
        rows2 = cur2.fetchall()
        keep_ids2: set[str] = set()
        seen_key2: set[tuple[str, str, str, str]] = set()
        for r in rows2:
            key = (r["user_id"], r["start_at"], r["end_at"], (r["title"] or "").strip())
            if key not in seen_key2:
                seen_key2.add(key)
                keep_ids2.add(r["id"])
        if rows2 and len(keep_ids2) < len(rows2):
            for r in rows2:
                if r["id"] not in keep_ids2:
                    conn.execute("DELETE FROM time_entries WHERE id = ?", (r["id"],))
    finally:
        conn.row_factory = None


def _delete_future_user_logs(conn: sqlite3.Connection) -> None:
    """Remove user-created logs for today and future (not from imported calendars). Keeps entries with planned_event_id."""
    cur = conn.execute("SELECT timezone FROM users WHERE id = ?", (SINGLE_USER_ID,))
    row = cur.fetchone()
    if not row:
        return
    try:
        tz = ZoneInfo(row["timezone"])
    except Exception:
        return
    now_local = _utc_now().astimezone(tz)
    start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_today_utc = start_of_today_local.astimezone(timezone.utc).isoformat()
    conn.execute(
        """
        DELETE FROM time_entries
        WHERE user_id = ? AND start_at >= ?
          AND (planned_event_id IS NULL OR planned_event_id = '')
        """,
        (SINGLE_USER_ID, start_of_today_utc),
    )


@dataclass
class PushSub:
    id: str
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str


def _get_prompt_categories(user_id: str) -> list[dict[str, Any]]:
    with _db() as conn:
        cur = conn.execute("PRAGMA table_info(categories)")
        has_color = "color" in [row[1] for row in cur.fetchall()]
        cur = conn.execute(
            """
            SELECT id, name, is_writable, sort_order
            """ + (", color" if has_color else "") + """
            FROM categories
            WHERE user_id = ? AND is_prompt_choice = 1
            ORDER BY sort_order ASC
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _ensure_category(
    conn: sqlite3.Connection,
    user_id: str,
    name: str,
    is_prompt_choice: int,
    is_writable: int,
    sort_order: int,
) -> None:
    cur = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND name = ?",
        (user_id, name),
    )
    if cur.fetchone():
        return
    cid = f"cat_{secrets.token_hex(8)}"
    conn.execute(
        """
        INSERT INTO categories
          (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            user_id,
            name,
            int(is_prompt_choice),
            int(is_writable),
            int(sort_order),
            _utc_now().isoformat(),
        ),
    )


def _get_or_create_category_id(conn: sqlite3.Connection, user_id: str, name: str) -> str:
    cur = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND name = ?",
        (user_id, name),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cid = f"cat_{secrets.token_hex(8)}"
    conn.execute(
        """
        INSERT INTO categories
          (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at)
        VALUES (?, ?, ?, 0, 1, 110, ?)
        """,
        (cid, user_id, name, _utc_now().isoformat()),
    )
    return cid


def _get_user(user_id: str) -> dict[str, Any]:
    with _db() as conn:
        cur = conn.execute("SELECT id, email, timezone FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(row)


def _get_user_tz(user_id: str) -> ZoneInfo:
    tz_name = _get_user(user_id).get("timezone") or "America/Los_Angeles"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("America/Los_Angeles")


def _day_bounds_utc(user_id: str, day: str | None) -> tuple[datetime, datetime]:
    """
    Returns (start_utc, end_utc) for the given local day (YYYY-MM-DD) in user's timezone.
    If day is None, uses user's "today".
    """
    tz = _get_user_tz(user_id)
    now_local = _utc_now().astimezone(tz)
    if day:
        try:
            y, m, d = [int(x) for x in day.split("-")]
            day_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
        except Exception:
            day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    start_local = day_local
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _get_push_subs(user_id: str) -> list[PushSub]:
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT id, endpoint, p256dh, auth, user_agent
            FROM push_subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return [PushSub(**dict(r)) for r in cur.fetchall()]


def _has_entries_in_last(user_id: str, minutes: int) -> bool:
    since = _utc_now() - timedelta(minutes=minutes)
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT COUNT(1)
            FROM time_entries
            WHERE user_id = ? AND end_at >= ?
            """,
            (user_id, since.isoformat()),
        )
        return int(cur.fetchone()[0]) > 0


def _send_push(user_id: str, title: str, body: str, url: str = "/") -> dict[str, Any]:
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return {"ok": False, "error": "VAPID keys not configured"}

    subs = _get_push_subs(user_id)
    if not subs:
        return {"ok": False, "error": "No push subscriptions"}

    payload = json.dumps({"title": title, "body": body, "url": url})
    vapid_claims = {"sub": VAPID_SUBJECT}

    results: list[dict[str, Any]] = []
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
            )
            results.append({"id": s.id, "ok": True})
        except WebPushException as exc:
            results.append({"id": s.id, "ok": False, "error": str(exc)})
    return {"ok": True, "results": results}


def _require_companion_token(request: Request) -> None:
    """
    If COMPANION_SYNC_TOKEN is configured, require it for companion sync calls.
    Accepts: Authorization: Bearer <token> or X-TimeSense-Companion-Token.
    """
    if not COMPANION_SYNC_TOKEN:
        return
    auth_header = (request.headers.get("authorization") or "").strip()
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = (request.headers.get("x-timesense-companion-token") or "").strip()
    if token != COMPANION_SYNC_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid companion token")


def _hourly_reminder_job() -> None:
    # MVP heuristic: if no entries ended in last 60 minutes, remind.
    if _has_entries_in_last(SINGLE_USER_ID, 60):
        return
    _send_push(
        SINGLE_USER_ID,
        title="TimeSense",
        body="Quick check: what did you do in the last hour?",
        url="/#quick",
    )


def _daily_review_job() -> None:
    _send_push(
        SINGLE_USER_ID,
        title="TimeSense",
        body="Daily review: fill today’s gaps (2–3 min).",
        url="/#review",
    )


def _start_scheduler() -> None:
    scheduler = BackgroundScheduler()
    scheduler.add_job(_hourly_reminder_job, "cron", minute=0)
    scheduler.add_job(_daily_review_job, "cron", hour=23, minute=0)
    scheduler.start()


@app.on_event("startup")
def on_startup() -> None:
    _ensure_db()
    _start_scheduler()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_frontend() -> HTMLResponse:
    # Read index from disk and inject cache-busting version from app.js mtime so updates are picked up
    index_path = STATIC_DIR / "index.html"
    app_js_path = STATIC_DIR / "app.js"
    html = index_path.read_text(encoding="utf-8")
    try:
        cache_bust = str(int(app_js_path.stat().st_mtime))
    except OSError:
        cache_bust = "20260209_5"
    html = re.sub(r'\?v=[^"\']+', f'?v={cache_bust}', html)
    html = html.replace("__CACHE_BUST__", cache_bust)
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, proxy-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Vary": "*",
        },
    )


@app.get("/manifest.webmanifest", response_class=FileResponse)
def serve_manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", headers={"Cache-Control": "no-store"})


@app.get("/sw.js", response_class=FileResponse)
def serve_sw() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon() -> Response:
    """Avoid 404 when browser requests favicon.ico."""
    return Response(status_code=204)


class WhoAmIResponse(BaseModel):
    user_id: str
    email: str
    timezone: str
    vapid_public_key: str
    google_connected: bool = False


@app.get("/api/me", response_model=WhoAmIResponse)
def api_me() -> WhoAmIResponse:
    u = _get_user(SINGLE_USER_ID)
    with _db() as conn:
        cur = conn.execute("SELECT 1 FROM google_tokens WHERE user_id = ?", (SINGLE_USER_ID,))
        google_connected = bool(cur.fetchone())
    return WhoAmIResponse(
        user_id=u["id"],
        email=u["email"],
        timezone=u["timezone"],
        vapid_public_key=VAPID_PUBLIC_KEY,
        google_connected=google_connected,
    )


class CategoryOut(BaseModel):
    id: str
    name: str
    is_writable: bool
    sort_order: int
    color: str | None = None


def _categories_include_color(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("PRAGMA table_info(categories)")
    return "color" in [row[1] for row in cur.fetchall()]


class CategoryCreate(BaseModel):
    name: str
    color: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


categories_router = APIRouter(prefix="/api/categories", tags=["categories"])


@categories_router.get("/prompt", response_model=list[CategoryOut])
def api_prompt_categories() -> list[CategoryOut]:
    cats = _get_prompt_categories(SINGLE_USER_ID)
    with _db() as conn:
        has_color = _categories_include_color(conn)
    return [
        CategoryOut(
            id=c["id"],
            name=c["name"],
            is_writable=bool(c["is_writable"]),
            sort_order=int(c["sort_order"]),
            color=c.get("color") if has_color else None,
        )
        for c in cats
    ]


@categories_router.get("/canonical", response_model=list[dict[str, str]])
def api_canonical_categories() -> list[dict[str, str]]:
    """Return the categorization sheet: canonical category names for review panel and targets."""
    return [{"name": n} for n in CANONICAL_CATEGORIES]


@categories_router.get("", response_model=list[CategoryOut])
def api_categories() -> list[CategoryOut]:
    with _db() as conn:
        cur = conn.execute("PRAGMA table_info(categories)")
        has_color = "color" in [row[1] for row in cur.fetchall()]
        cur = conn.execute(
            """
            SELECT id, name, is_writable, sort_order
            """ + (", color" if has_color else "") + """
            FROM categories
            WHERE user_id = ?
            ORDER BY sort_order ASC
            """,
            (SINGLE_USER_ID,),
        )
        rows = cur.fetchall()
    return [
        CategoryOut(
            id=r["id"],
            name=r["name"],
            is_writable=bool(r["is_writable"]),
            sort_order=int(r["sort_order"]),
            color=(dict(r).get("color") if has_color else None),
        )
        for r in rows
    ]


@categories_router.post("", response_model=CategoryOut)
def api_create_category(req: CategoryCreate) -> CategoryOut:
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    cid = f"cat_{secrets.token_hex(8)}"
    with _db() as conn:
        cur = conn.execute("PRAGMA table_info(categories)")
        has_color = "color" in [row[1] for row in cur.fetchall()]
        cur = conn.execute("SELECT id FROM categories WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?)", (SINGLE_USER_ID, name))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A category with this name already exists")
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM categories WHERE user_id = ?", (SINGLE_USER_ID,)).fetchone()[0]
        if has_color:
            conn.execute(
                """
                INSERT INTO categories (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at, color)
                VALUES (?, ?, ?, 0, 1, ?, ?, ?)
                """,
                (cid, SINGLE_USER_ID, name, int(max_order) + 1, _utc_now().isoformat(), req.color),
            )
        else:
            conn.execute(
                """
                INSERT INTO categories (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at)
                VALUES (?, ?, ?, 0, 1, ?, ?)
                """,
                (cid, SINGLE_USER_ID, name, int(max_order) + 1, _utc_now().isoformat()),
            )
        conn.commit()
        cur = conn.execute(
            "SELECT id, name, is_writable, sort_order" + (", color" if has_color else "") + " FROM categories WHERE id = ?",
            (cid,),
        )
        row = cur.fetchone()
    return CategoryOut(
        id=row["id"],
        name=row["name"],
        is_writable=bool(row["is_writable"]),
        sort_order=int(row["sort_order"]),
        color=(dict(row).get("color") if has_color else None),
    )


@categories_router.patch("/{category_id}", response_model=CategoryOut)
def api_update_category(category_id: str, req: CategoryUpdate) -> CategoryOut:
    with _db() as conn:
        cur = conn.execute("PRAGMA table_info(categories)")
        has_color = "color" in [row[1] for row in cur.fetchall()]
        cur = conn.execute("SELECT id, name, is_writable, sort_order FROM categories WHERE id = ? AND user_id = ?", (category_id, SINGLE_USER_ID))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")
        updates = []
        params = []
        if req.name is not None:
            name = (req.name or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="Category name cannot be empty")
            updates.append("name = ?")
            params.append(name)
        if has_color and req.color is not None:
            updates.append("color = ?")
            params.append(req.color)
        if not updates:
            cur = conn.execute("SELECT id, name, is_writable, sort_order" + (", color" if has_color else "") + " FROM categories WHERE id = ?", (category_id,))
            r = cur.fetchone()
            return CategoryOut(id=r["id"], name=r["name"], is_writable=bool(r["is_writable"]), sort_order=int(r["sort_order"]), color=(dict(r).get("color") if has_color else None))
        params.append(category_id)
        conn.execute(
            "UPDATE categories SET " + ", ".join(updates) + " WHERE id = ? AND user_id = ?",
            params + [SINGLE_USER_ID],
        )
        conn.commit()
        cur = conn.execute("SELECT id, name, is_writable, sort_order" + (", color" if has_color else "") + " FROM categories WHERE id = ?", (category_id,))
        r = cur.fetchone()
    return CategoryOut(id=r["id"], name=r["name"], is_writable=bool(r["is_writable"]), sort_order=int(r["sort_order"]), color=(dict(r).get("color") if has_color else None))


@categories_router.delete("/{category_id}")
def api_delete_category(category_id: str) -> None:
    with _db() as conn:
        cur = conn.execute("SELECT id FROM categories WHERE id = ? AND user_id = ?", (category_id, SINGLE_USER_ID))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Category not found")
        cur = conn.execute("SELECT 1 FROM time_entries WHERE category_id = ? LIMIT 1", (category_id,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Cannot delete category that has time entries. Reassign or remove entries first.")
        cur = conn.execute("SELECT 1 FROM weekly_targets WHERE category = (SELECT name FROM categories WHERE id = ?) LIMIT 1", (category_id,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Cannot delete category used in a weekly target. Remove the target first.")
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (category_id, SINGLE_USER_ID))
        conn.commit()
    return None


app.include_router(categories_router)


class QuickLogRequest(BaseModel):
    category_id: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    device: Literal["ios_pwa", "web", "mac_menubar"] = "web"
    start_at: datetime | None = None
    end_at: datetime | None = None
    source: Literal["manual", "prompt", "review"] = "manual"
    planned_event_id: str | None = None  # when converting a planned event to log, so we can hide the planned one


class TimeEntryOut(BaseModel):
    id: str
    start_at: datetime
    end_at: datetime
    title: str
    category_id: str
    category_name: str = ""
    tags: list[str]
    source: str
    device: str


def _get_last_boundary(user_id: str) -> datetime:
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT end_at FROM time_entries
            WHERE user_id = ?
            ORDER BY end_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return _utc_now() - timedelta(minutes=15)
        try:
            return datetime.fromisoformat(row["end_at"])
        except Exception:
            return _utc_now() - timedelta(minutes=15)


@app.post("/api/quick_log", response_model=TimeEntryOut)
def api_quick_log(req: QuickLogRequest) -> TimeEntryOut:
    now = _utc_now()
    start_at = req.start_at or _get_last_boundary(SINGLE_USER_ID)
    end_at = req.end_at or now
    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="end_at must be after start_at")

    entry_id = f"te_{secrets.token_hex(10)}"
    with _db() as conn:
        # ensure category exists for user (simple guard)
        cur = conn.execute(
            "SELECT id, name, is_writable FROM categories WHERE id = ? AND user_id = ?",
            (req.category_id, SINGLE_USER_ID),
        )
        cat = cur.fetchone()
        if not cat:
            if (req.title or "").strip().lower() == "sleep":
                sleep_id = _get_or_create_category_id(conn, SINGLE_USER_ID, "Sleep")
                cur = conn.execute(
                    "SELECT id, name, is_writable FROM categories WHERE id = ? AND user_id = ?",
                    (sleep_id, SINGLE_USER_ID),
                )
                cat = cur.fetchone()
                req.category_id = sleep_id
            if not cat:
                raise HTTPException(status_code=400, detail="Unknown category_id")
        conn.execute(
            """
            INSERT INTO time_entries
              (id, user_id, start_at, end_at, title, category_id, tags_json, source, device, created_at, planned_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                SINGLE_USER_ID,
                start_at.isoformat(),
                end_at.isoformat(),
                (req.title or "").strip(),
                req.category_id,
                json.dumps(req.tags),
                req.source,
                req.device,
                _utc_now().isoformat(),
                (req.planned_event_id or "").strip() or None,
            ),
        )
        conn.commit()

    out = TimeEntryOut(
        id=entry_id,
        start_at=start_at,
        end_at=end_at,
        title=(req.title or "").strip(),
        category_id=req.category_id,
        category_name=cat["name"] if cat else "",
        tags=req.tags,
        source=req.source,
        device=req.device,
    )

    # MVP: if Google connected and category is writable, write back to TimeSense Logs calendar.
    try:
        if bool(cat["is_writable"]):
            with _db() as conn:
                cur2 = conn.execute("SELECT 1 FROM google_tokens WHERE user_id = ?", (SINGLE_USER_ID,))
                if cur2.fetchone():
                    _writeback_time_entry(SINGLE_USER_ID, out)
    except Exception:
        # best-effort writeback; logging can be added later
        pass

    return out


class DayReviewOut(BaseModel):
    day: str
    timezone: str
    start_utc: datetime
    end_utc: datetime
    entries: list[TimeEntryOut]
    gaps: list[dict[str, datetime]]


@app.get("/api/day_review", response_model=DayReviewOut)
def api_day_review(day: str | None = None) -> DayReviewOut:
    u = _get_user(SINGLE_USER_ID)
    tz = u["timezone"]
    start_utc, end_utc = _day_bounds_utc(SINGLE_USER_ID, day)
    now_utc = _utc_now()
    effective_end = min(end_utc, now_utc)  # for "today", don't show future gaps

    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.id, te.start_at, te.end_at, te.title, te.category_id, te.tags_json, te.source, te.device,
                   c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, effective_end.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()

    entries: list[TimeEntryOut] = []
    for r in rows:
        # Parse datetimes and ensure they're timezone-aware (UTC)
        s_dt = datetime.fromisoformat(r["start_at"])
        e_dt = datetime.fromisoformat(r["end_at"])
        if s_dt.tzinfo is None:
            s_dt = s_dt.replace(tzinfo=timezone.utc)
        if e_dt.tzinfo is None:
            e_dt = e_dt.replace(tzinfo=timezone.utc)
        entries.append(
            TimeEntryOut(
                id=r["id"],
                start_at=s_dt,
                end_at=e_dt,
                title=r["title"] or "",
                category_id=r["category_id"],
                category_name=r["category_name"] or "",
                tags=json.loads(r["tags_json"] or "[]"),
                source=r["source"],
                device=r["device"],
            )
        )

    # Compute gaps (clipped to [start_utc, effective_end])
    gaps: list[dict[str, datetime]] = []
    cursor = start_utc
    for e in entries:
        s = max(e.start_at, start_utc)
        t = min(e.end_at, effective_end)
        if t <= start_utc or s >= effective_end:
            continue
        if s > cursor:
            gaps.append({"start_at": cursor, "end_at": s})
        if t > cursor:
            cursor = t
    if cursor < effective_end:
        gaps.append({"start_at": cursor, "end_at": effective_end})

    # determine response day string in local tz
    tzinfo = _get_user_tz(SINGLE_USER_ID)
    day_local = start_utc.astimezone(tzinfo).date().isoformat()
    return DayReviewOut(
        day=day_local,
        timezone=tz,
        start_utc=start_utc,
        end_utc=end_utc,
        entries=entries,
        gaps=gaps,
    )


def _require_google_config() -> None:
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(
            status_code=400,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    if not SESSION_SECRET:
        raise HTTPException(
            status_code=400,
            detail="SESSION_SECRET not configured (required for OAuth state).",
        )


def _require_ms_config() -> None:
    if not (MS_CLIENT_ID and MS_CLIENT_SECRET):
        raise HTTPException(
            status_code=400,
            detail="Microsoft OAuth not configured. Set MS_CLIENT_ID and MS_CLIENT_SECRET.",
        )
    if not SESSION_SECRET:
        raise HTTPException(
            status_code=400,
            detail="SESSION_SECRET not configured (required for OAuth state).",
        )


def _ms_token_row(user_id: str) -> sqlite3.Row | None:
    with _db() as conn:
        cur = conn.execute(
            "SELECT access_token, refresh_token, expires_at, scope, last_error FROM ms_tokens WHERE user_id = ?",
            (user_id,),
        )
        return cur.fetchone()


def _ms_save_token(user_id: str, token: dict[str, Any], error: str = "") -> None:
    now = _utc_now().isoformat()
    expires_in = int(token.get("expires_in") or 0)
    expires_at = (_utc_now() + timedelta(seconds=max(0, expires_in - 30))).isoformat()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO ms_tokens (user_id, access_token, refresh_token, expires_at, scope, created_at, updated_at, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              access_token=excluded.access_token,
              refresh_token=CASE
                WHEN excluded.refresh_token != '' THEN excluded.refresh_token
                ELSE ms_tokens.refresh_token
              END,
              expires_at=excluded.expires_at,
              scope=excluded.scope,
              updated_at=excluded.updated_at,
              last_error=excluded.last_error
            """,
            (
                user_id,
                token.get("access_token", "") or "",
                token.get("refresh_token", "") or "",
                expires_at,
                token.get("scope", "") or "",
                now,
                now,
                error,
            ),
        )
        conn.commit()


def _ms_get_access_token(user_id: str) -> str:
    row = _ms_token_row(user_id)
    if not row:
        raise HTTPException(status_code=401, detail="Outlook not connected")
    access = row["access_token"] or ""
    refresh = row["refresh_token"] or ""
    expires_at = row["expires_at"] or ""
    if not access:
        raise HTTPException(status_code=401, detail="Outlook not connected")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if _utc_now() < exp:
                return access
        except Exception:
            pass
    if not refresh:
        raise HTTPException(status_code=401, detail="Outlook token expired; reconnect required")

    token_url = f"{MS_AUTH_BASE}/token"
    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "scope": " ".join(MS_SCOPES),
        "redirect_uri": MS_REDIRECT_URI,
    }
    resp = requests.post(token_url, data=data, timeout=15)
    if not resp.ok:
        _ms_save_token(user_id, {"access_token": access, "refresh_token": refresh, "expires_in": 0, "scope": row["scope"]}, error=resp.text[:500])
        raise HTTPException(status_code=401, detail="Outlook token refresh failed; reconnect required")
    token = resp.json()
    _ms_save_token(user_id, token, error="")
    return token.get("access_token", "")


def _fetch_outlook_events(start_utc: datetime, end_utc: datetime) -> list["PlannedEventOut"]:
    """
    Fetch Outlook calendar view events via Microsoft Graph and return as PlannedEventOut (UTC datetimes).
    """
    access_token = _ms_get_access_token(SINGLE_USER_ID)
    url = "https://graph.microsoft.com/v1.0/me/calendarView"
    params = {
        "startDateTime": start_utc.isoformat().replace("+00:00", "Z"),
        "endDateTime": end_utc.isoformat().replace("+00:00", "Z"),
        "$select": "id,subject,start,end,isAllDay,categories",
        "$top": "200",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        # Ask Graph to return times in UTC to simplify parsing
        "Prefer": 'outlook.timezone="UTC"',
    }
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    if not resp.ok:
        # Persist error for UI
        _ms_save_token(SINGLE_USER_ID, {"access_token": access_token, "refresh_token": "", "expires_in": 0, "scope": " ".join(MS_SCOPES)}, error=resp.text[:500])
        raise HTTPException(status_code=502, detail="Outlook calendar fetch failed")

    data = resp.json()
    values = data.get("value", []) or []
    out: list[PlannedEventOut] = []
    for ev in values:
        ev_id = ev.get("id", "") or f"ms_{secrets.token_hex(8)}"
        summary = ev.get("subject", "") or ""
        start = (ev.get("start") or {}).get("dateTime", "")
        end = (ev.get("end") or {}).get("dateTime", "")
        if not start or not end:
            continue
        try:
            # Graph returns e.g. "2026-01-21T17:00:00.0000000"
            sdt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            edt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            # Ensure UTC-aware
            if sdt.tzinfo is None:
                sdt = sdt.replace(tzinfo=timezone.utc)
            else:
                sdt = sdt.astimezone(timezone.utc)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
            else:
                edt = edt.astimezone(timezone.utc)
        except Exception:
            continue
        if edt <= start_utc or sdt >= end_utc:
            continue
        out.append(
            PlannedEventOut(
                id=ev_id,
                start_at=max(sdt, start_utc),
                end_at=min(edt, end_utc),
                summary=summary,
                color_id="",
                source="outlook",
                source_calendar_name="Outlook",
            )
        )
    return out


def _fetch_imported_planned(start_utc: datetime, end_utc: datetime) -> list["PlannedEventOut"]:
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT id, title, start_at, end_at, source, source_calendar_name
            FROM planned_events_imported
            WHERE user_id = ?
              AND start_at < ?
              AND end_at > ?
            ORDER BY start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()

    out: list[PlannedEventOut] = []
    for r in rows:
        try:
            sdt = datetime.fromisoformat(r["start_at"])
            edt = datetime.fromisoformat(r["end_at"])
            # Ensure timezone-aware (UTC)
            if sdt.tzinfo is None:
                sdt = sdt.replace(tzinfo=timezone.utc)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        title = r["title"] or ""
        source = r["source"] if r["source"] in ("apple_ics", "apple_eventkit") else "apple_ics"
        out.append(
            PlannedEventOut(
                id=r["id"],
                start_at=max(sdt, start_utc),
                end_at=min(edt, end_utc),
                summary=title,
                color_id="",
                source=source,
                source_calendar_name=r["source_calendar_name"] or "Apple Calendar",
                suggested_category=_smart_categorize(title, ""),
            )
        )
    return out


class ApplePlannedEventIn(BaseModel):
    external_id: str
    title: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool = False
    source_calendar_name: str = ""


class AppleSyncUpsertRequest(BaseModel):
    range_start: datetime
    range_end: datetime
    events: list[ApplePlannedEventIn]


class AppleSyncUpsertOut(BaseModel):
    ok: bool
    deleted: int
    upserted: int


@app.post("/api/apple_sync/planned_upsert", response_model=AppleSyncUpsertOut)
def api_apple_sync_planned_upsert(request: Request, req: AppleSyncUpsertRequest) -> AppleSyncUpsertOut:
    """
    iPhone companion sync (EventKit, read-only).
    Strategy: delete existing Apple-synced planned events in the range, then upsert incoming list.
    """
    _require_companion_token(request)
    
    # Debug logging
    print(f"[SYNC] Received {len(req.events)} events")
    print(f"[SYNC] Range: {req.range_start} to {req.range_end}")
    if req.events:
        # Log a few sample events to see their dates
        for ev in req.events[:5]:
            print(f"[SYNC]   Event: {ev.title[:30] if ev.title else 'No title'} | {ev.start_at} - {ev.end_at}")
        # Find newest event
        if len(req.events) > 1:
            newest = max(req.events, key=lambda x: x.start_at)
            print(f"[SYNC]   Newest event: {newest.title[:30] if newest.title else 'No title'} | {newest.start_at}")
    
    if req.range_end <= req.range_start:
        raise HTTPException(status_code=400, detail="range_end must be after range_start")

    start_utc = req.range_start
    end_utc = req.range_end
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    else:
        start_utc = start_utc.astimezone(timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)
    else:
        end_utc = end_utc.astimezone(timezone.utc)

    now = _utc_now().isoformat()
    deleted = 0
    upserted = 0

    with _db() as conn:
        cur = conn.execute(
            """
            DELETE FROM planned_events_imported
            WHERE user_id = ? AND source = 'apple_eventkit'
              AND start_at < ? AND end_at > ?
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        deleted = cur.rowcount if cur.rowcount is not None else 0

        for ev in req.events:
            if not ev.external_id:
                continue
            s = ev.start_at
            e = ev.end_at
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            else:
                s = s.astimezone(timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            else:
                e = e.astimezone(timezone.utc)
            if e <= s:
                continue

            # Use composite external_id so recurring events (same EventKit id, different start) each get a row
            external_id_stored = f"{ev.external_id}_{s.isoformat()}"
            row_id = f"pe_{secrets.token_hex(10)}"
            conn.execute(
                """
                INSERT INTO planned_events_imported
                  (id, user_id, source, external_id, source_calendar_name, title, is_all_day, start_at, end_at, created_at, updated_at)
                VALUES (?, ?, 'apple_eventkit', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source, external_id) DO UPDATE SET
                  source_calendar_name=excluded.source_calendar_name,
                  title=excluded.title,
                  is_all_day=excluded.is_all_day,
                  start_at=excluded.start_at,
                  end_at=excluded.end_at,
                  updated_at=excluded.updated_at
                """,
                (
                    row_id,
                    SINGLE_USER_ID,
                    external_id_stored,
                    ev.source_calendar_name or "",
                    (ev.title or "Planned").strip(),
                    int(ev.is_all_day),
                    s.isoformat(),
                    e.isoformat(),
                    now,
                    now,
                ),
            )
            upserted += 1
        conn.commit()

    return AppleSyncUpsertOut(ok=True, deleted=deleted, upserted=upserted)


class AppleSyncStatusOut(BaseModel):
    connected: bool
    last_sync_at: str = ""
    total_events: int = 0


@app.get("/api/apple_sync/status", response_model=AppleSyncStatusOut)
def api_apple_sync_status() -> AppleSyncStatusOut:
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT COUNT(1) AS total, MAX(updated_at) AS last_sync
            FROM planned_events_imported
            WHERE user_id = ? AND source = 'apple_eventkit'
            """,
            (SINGLE_USER_ID,),
        )
        row = cur.fetchone()
    total = int(row["total"]) if row and row["total"] is not None else 0
    last_sync = row["last_sync"] or ""
    return AppleSyncStatusOut(connected=total > 0, last_sync_at=last_sync, total_events=total)


class IcsImportOut(BaseModel):
    ok: bool
    imported: int


@app.post("/api/apple_calendar/ics_import", response_model=IcsImportOut)
def api_apple_calendar_ics_import(file: UploadFile = File(...)) -> IcsImportOut:
    """
    Import Apple Calendar-exported .ics file (read-only planned blocks).
    Title only (privacy scope A).
    """
    ics_bytes = file.file.read()
    if not ics_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        cal = ICalendar.from_ical(ics_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ICS: {exc}") from exc

    now = _utc_now().isoformat()
    imported = 0
    with _db() as conn:
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            summary = str(component.get("SUMMARY") or "").strip()
            if not summary:
                summary = "Planned"

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if not dtstart or not dtend:
                continue

            try:
                s = dtstart.dt
                e = dtend.dt
            except Exception:
                continue

            # Convert to UTC datetimes
            if isinstance(s, datetime):
                sdt = s if s.tzinfo else s.replace(tzinfo=timezone.utc)
                sdt = sdt.astimezone(timezone.utc)
                is_all_day = 0
            else:
                # date
                sdt = datetime(s.year, s.month, s.day, tzinfo=timezone.utc)
                is_all_day = 1

            if isinstance(e, datetime):
                edt = e if e.tzinfo else e.replace(tzinfo=timezone.utc)
                edt = edt.astimezone(timezone.utc)
            else:
                edt = datetime(e.year, e.month, e.day, tzinfo=timezone.utc)

            if edt <= sdt:
                continue

            ext_uid = str(component.get("UID") or "").strip() or f"uid_{secrets.token_hex(8)}"
            row_id = f"pe_{secrets.token_hex(10)}"

            conn.execute(
                """
                INSERT INTO planned_events_imported
                  (id, user_id, source, external_id, source_calendar_name, title, is_all_day, start_at, end_at, created_at, updated_at)
                VALUES (?, ?, 'apple_ics', ?, '', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source, external_id) DO UPDATE SET
                  title=excluded.title,
                  is_all_day=excluded.is_all_day,
                  start_at=excluded.start_at,
                  end_at=excluded.end_at,
                  updated_at=excluded.updated_at
                """,
                (
                    row_id,
                    SINGLE_USER_ID,
                    ext_uid,
                    summary,
                    int(is_all_day),
                    sdt.isoformat(),
                    edt.isoformat(),
                    now,
                    now,
                ),
            )
            imported += 1
        conn.commit()

    return IcsImportOut(ok=True, imported=imported)


def _get_google_creds(user_id: str) -> Credentials:
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT access_token, refresh_token, token_uri, client_id, client_secret, scopes_json, expiry
            FROM google_tokens
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Google not connected")

    scopes = json.loads(row["scopes_json"] or "[]")
    creds = Credentials(
        token=row["access_token"] or None,
        refresh_token=row["refresh_token"] or None,
        token_uri=row["token_uri"] or "https://oauth2.googleapis.com/token",
        client_id=row["client_id"] or GOOGLE_CLIENT_ID,
        client_secret=row["client_secret"] or GOOGLE_CLIENT_SECRET,
        scopes=scopes,
    )
    if row["expiry"]:
        try:
            creds.expiry = datetime.fromisoformat(row["expiry"])
        except Exception:
            pass

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            with _db() as conn:
                conn.execute(
                    """
                    UPDATE google_tokens
                    SET access_token = ?, expiry = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        creds.token or "",
                        creds.expiry.isoformat() if getattr(creds, "expiry", None) else "",
                        _utc_now().isoformat(),
                        user_id,
                    ),
                )
                conn.commit()
        else:
            raise HTTPException(status_code=401, detail="Google credentials invalid; reconnect required")
    return creds


def _google_calendar_service(user_id: str):
    creds = _get_google_creds(user_id)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _get_google_calendar_state(user_id: str) -> dict[str, str]:
    with _db() as conn:
        cur = conn.execute(
            "SELECT planned_calendar_id, logs_calendar_id FROM google_calendar_state WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            now = _utc_now().isoformat()
            conn.execute(
                """
                INSERT INTO google_calendar_state (user_id, planned_calendar_id, logs_calendar_id, created_at, updated_at)
                VALUES (?, 'primary', '', ?, ?)
                """,
                (user_id, now, now),
            )
            conn.commit()
            return {"planned_calendar_id": "primary", "logs_calendar_id": ""}
        return {"planned_calendar_id": row["planned_calendar_id"], "logs_calendar_id": row["logs_calendar_id"]}


def _set_logs_calendar_id(user_id: str, calendar_id: str) -> None:
    now = _utc_now().isoformat()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO google_calendar_state (user_id, planned_calendar_id, logs_calendar_id, created_at, updated_at)
            VALUES (?, 'primary', ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET logs_calendar_id=excluded.logs_calendar_id, updated_at=excluded.updated_at
            """,
            (user_id, calendar_id, now, now),
        )
        conn.commit()


def _ensure_timesense_logs_calendar(user_id: str) -> str:
    state = _get_google_calendar_state(user_id)
    if state.get("logs_calendar_id"):
        return state["logs_calendar_id"]

    svc = _google_calendar_service(user_id)
    page_token = None
    while True:
        resp = svc.calendarList().list(pageToken=page_token).execute()
        for item in resp.get("items", []):
            if item.get("summary") == "TimeSense Logs":
                cal_id = item.get("id", "")
                if cal_id:
                    _set_logs_calendar_id(user_id, cal_id)
                    return cal_id
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    try:
        created = svc.calendars().insert(
            body={"summary": "TimeSense Logs", "timeZone": _get_user(user_id)["timezone"]}
        ).execute()
    except HttpError as exc:
        # Most common: connected without the full calendar scope; user must re-consent.
        if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 403:
            raise HTTPException(
                status_code=403,
                detail="Google connected, but missing permission to create calendars. Please reconnect Google (new scope) and try again.",
            ) from exc
        raise
    cal_id = created.get("id", "")
    if not cal_id:
        raise HTTPException(status_code=502, detail="Failed to create TimeSense Logs calendar")
    _set_logs_calendar_id(user_id, cal_id)
    return cal_id


def _round_down_to_minutes(dt: datetime, minutes: int) -> datetime:
    if minutes <= 1:
        return dt.replace(second=0, microsecond=0)
    dt = dt.replace(second=0, microsecond=0)
    discard = dt.minute % minutes
    return dt.replace(minute=dt.minute - discard)


def _round_up_to_minutes(dt: datetime, minutes: int) -> datetime:
    if minutes <= 1:
        return dt.replace(second=0, microsecond=0)
    dt = dt.replace(second=0, microsecond=0)
    mod = dt.minute % minutes
    if mod == 0:
        return dt
    return dt + timedelta(minutes=(minutes - mod))


def _writeback_time_entry(user_id: str, entry: "TimeEntryOut") -> None:
    logs_cal_id = _ensure_timesense_logs_calendar(user_id)
    svc = _google_calendar_service(user_id)
    tz = _get_user_tz(user_id)

    start_local = entry.start_at.replace(tzinfo=timezone.utc).astimezone(tz)
    end_local = entry.end_at.replace(tzinfo=timezone.utc).astimezone(tz)
    # Keep calendar tidy with 15-min alignment, but avoid shrinking duration.
    start_local = _round_down_to_minutes(start_local, 15)
    end_local = _round_up_to_minutes(end_local, 15)
    if end_local <= start_local:
        end_local = start_local + timedelta(minutes=15)

    summary = entry.category_name or "TimeSense"
    if entry.title:
        summary = f"{summary}: {entry.title}"

    description = ""
    if entry.tags:
        description = f"Tags: {', '.join(entry.tags)}"

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_local.isoformat(), "timeZone": str(tz.key)},
        "end": {"dateTime": end_local.isoformat(), "timeZone": str(tz.key)},
    }
    svc.events().insert(calendarId=logs_cal_id, body=body).execute()


@app.get("/auth/google/start")
def google_oauth_start(request: Request) -> RedirectResponse:
    _require_google_config()
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth dependency missing: {exc}") from exc

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_CAL_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session["google_oauth_state"] = state
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
def google_oauth_callback(request: Request, code: str | None = None, state: str | None = None) -> RedirectResponse:
    _require_google_config()
    expected_state = request.session.get("google_oauth_state")
    if not expected_state or not state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Please try again.")
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code.")

    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth dependency missing: {exc}") from exc

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_CAL_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    with _db() as conn:
        now = _utc_now().isoformat()
        conn.execute(
            """
            INSERT INTO google_tokens
              (user_id, access_token, refresh_token, token_uri, client_id, client_secret, scopes_json, expiry, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              access_token=excluded.access_token,
              refresh_token=CASE
                WHEN excluded.refresh_token != '' THEN excluded.refresh_token
                ELSE google_tokens.refresh_token
              END,
              token_uri=excluded.token_uri,
              client_id=excluded.client_id,
              client_secret=excluded.client_secret,
              scopes_json=excluded.scopes_json,
              expiry=excluded.expiry,
              updated_at=excluded.updated_at
            """,
            (
                SINGLE_USER_ID,
                creds.token or "",
                creds.refresh_token or "",
                getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
                GOOGLE_CLIENT_ID,
                GOOGLE_CLIENT_SECRET,
                json.dumps(list(creds.scopes or [])),
                creds.expiry.isoformat() if getattr(creds, "expiry", None) else "",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO google_calendar_state (user_id, planned_calendar_id, logs_calendar_id, created_at, updated_at)
            VALUES (?, 'primary', '', ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (SINGLE_USER_ID, now, now),
        )
        conn.commit()

    # Return to app
    return RedirectResponse(url="/")


@app.post("/api/google/disconnect")
def api_google_disconnect() -> JSONResponse:
    """
    Clears stored Google tokens so the next connect forces a full re-consent with updated scopes.
    """
    with _db() as conn:
        conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (SINGLE_USER_ID,))
        conn.execute(
            "UPDATE google_calendar_state SET logs_calendar_id = '', updated_at = ? WHERE user_id = ?",
            (_utc_now().isoformat(), SINGLE_USER_ID),
        )
        conn.commit()
    return JSONResponse({"ok": True})


@app.get("/auth/microsoft/start")
def ms_oauth_start(request: Request) -> RedirectResponse:
    _require_ms_config()
    state = secrets.token_urlsafe(24)
    request.session["ms_oauth_state"] = state
    auth_url = f"{MS_AUTH_BASE}/authorize"
    params = {
        "client_id": MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MS_REDIRECT_URI,
        "response_mode": "query",
        "scope": " ".join(MS_SCOPES),
        "state": state,
        "prompt": "select_account",
    }
    url = requests.Request("GET", auth_url, params=params).prepare().url
    return RedirectResponse(url)


@app.get("/auth/microsoft/callback")
def ms_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    _require_ms_config()
    expected = request.session.get("ms_oauth_state")
    if not expected or not state or state != expected:
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Please try again.")

    if error:
        msg = (error_description or error)[:500]
        _ms_save_token(SINGLE_USER_ID, {"access_token": "", "refresh_token": "", "expires_in": 0, "scope": ""}, error=msg)
        return RedirectResponse(url="/")

    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code.")

    token_url = f"{MS_AUTH_BASE}/token"
    data = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MS_REDIRECT_URI,
        "scope": " ".join(MS_SCOPES),
    }
    resp = requests.post(token_url, data=data, timeout=15)
    if not resp.ok:
        _ms_save_token(SINGLE_USER_ID, {"access_token": "", "refresh_token": "", "expires_in": 0, "scope": ""}, error=resp.text[:500])
        return RedirectResponse(url="/")
    token = resp.json()
    _ms_save_token(SINGLE_USER_ID, token, error="")
    return RedirectResponse(url="/")


class OutlookStatusOut(BaseModel):
    connected: bool
    last_error: str = ""


@app.get("/api/outlook/status", response_model=OutlookStatusOut)
def api_outlook_status() -> OutlookStatusOut:
    row = _ms_token_row(SINGLE_USER_ID)
    if not row:
        return OutlookStatusOut(connected=False, last_error="")
    connected = bool(row["access_token"])
    return OutlookStatusOut(connected=connected, last_error=row["last_error"] or "")


@app.post("/api/outlook/disconnect")
def api_outlook_disconnect() -> JSONResponse:
    with _db() as conn:
        conn.execute("DELETE FROM ms_tokens WHERE user_id = ?", (SINGLE_USER_ID,))
        conn.commit()
    return JSONResponse({"ok": True})


class GoogleStatusOut(BaseModel):
    connected: bool
    planned_calendar_id: str = "primary"
    logs_calendar_id: str = ""


@app.get("/api/google/status", response_model=GoogleStatusOut)
def api_google_status() -> GoogleStatusOut:
    with _db() as conn:
        cur = conn.execute("SELECT 1 FROM google_tokens WHERE user_id = ?", (SINGLE_USER_ID,))
        connected = bool(cur.fetchone())
        cur2 = conn.execute(
            "SELECT planned_calendar_id, logs_calendar_id FROM google_calendar_state WHERE user_id = ?",
            (SINGLE_USER_ID,),
        )
        row = cur2.fetchone()
    if not row:
        return GoogleStatusOut(connected=connected)
    return GoogleStatusOut(
        connected=connected,
        planned_calendar_id=row["planned_calendar_id"],
        logs_calendar_id=row["logs_calendar_id"],
    )


class GoogleSetupOut(BaseModel):
    ok: bool
    logs_calendar_id: str


@app.post("/api/google/setup", response_model=GoogleSetupOut)
def api_google_setup() -> GoogleSetupOut:
    _require_google_config()
    cal_id = _ensure_timesense_logs_calendar(SINGLE_USER_ID)
    return GoogleSetupOut(ok=True, logs_calendar_id=cal_id)


class PlannedEventOut(BaseModel):
    id: str
    start_at: datetime
    end_at: datetime
    summary: str = ""
    color_id: str = ""
    source: Literal["google", "outlook", "apple_ics", "apple_eventkit"] = "google"
    source_calendar_name: str = ""
    suggested_category: str = ""  # For display/color: Work, Learning, etc. from title


@app.get("/api/planned_events", response_model=list[PlannedEventOut])
def api_planned_events(day: str | None = None) -> list[PlannedEventOut]:
    """
    Fetch planned events from Google Calendar (primary) for the given local day (YYYY-MM-DD).
    """
    svc = None
    try:
        _get_google_creds(SINGLE_USER_ID)
        svc = _google_calendar_service(SINGLE_USER_ID)
    except Exception:
        svc = None
    start_utc, end_utc = _day_bounds_utc(SINGLE_USER_ID, day)
    tz = _get_user_tz(SINGLE_USER_ID)

    items: list[dict[str, Any]] = []
    if svc:
        page_token = None
        while True:
            resp = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=start_utc.isoformat(),
                    timeMax=end_utc.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            items.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    out: list[PlannedEventOut] = []
    for ev in items:
        ev_id = ev.get("id", "") or f"ev_{secrets.token_hex(8)}"
        summary = ev.get("summary", "") or ""
        color_id = ev.get("colorId", "") or ""
        s = ev.get("start", {}) or {}
        e = ev.get("end", {}) or {}

        # timed events: RFC3339 dateTime
        if "dateTime" in s and "dateTime" in e:
            try:
                sdt = datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
                edt = datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
        # all-day events: date (YYYY-MM-DD) in calendar timezone
        elif "date" in s and "date" in e:
            try:
                s_local = datetime.fromisoformat(s["date"]).replace(tzinfo=tz)
                e_local = datetime.fromisoformat(e["date"]).replace(tzinfo=tz)
                sdt = s_local.astimezone(timezone.utc)
                edt = e_local.astimezone(timezone.utc)
            except Exception:
                continue
        else:
            continue

        # Clip to day bounds
        if edt <= start_utc or sdt >= end_utc:
            continue
        out.append(
            PlannedEventOut(
                id=ev_id,
                start_at=max(sdt, start_utc),
                end_at=min(edt, end_utc),
                summary=summary,
                color_id=color_id,
            )
        )

    # Merge Outlook planned events if connected
    try:
        ms_row = _ms_token_row(SINGLE_USER_ID)
        if ms_row and ms_row["access_token"]:
            out.extend(_fetch_outlook_events(start_utc, end_utc))
    except Exception:
        pass

    # Merge imported Apple Calendar ICS planned events
    out.extend(_fetch_imported_planned(start_utc, end_utc))

    return out


class PlannedEventRangeOut(PlannedEventOut):
    day: str  # local date YYYY-MM-DD (user timezone)


@app.get("/api/planned_events_range", response_model=list[PlannedEventRangeOut])
def api_planned_events_range(
    start_day: str | None = None,
    days: int = 7,
    include_google: str = "false",
    include_outlook: str = "false",
) -> list[PlannedEventRangeOut]:
    """
    Fetch planned events for a local date range.
    Default: only Apple Calendar + imported ICS (no Google, no Outlook) to avoid duplicates when Apple already syncs them.
    - start_day: YYYY-MM-DD in user's timezone (defaults to today)
    - days: number of days forward (default 7)
    - include_google: "true" to add Google Calendar events
    - include_outlook: "true" to add Outlook calendar events
    """
    if days < 1 or days > 31:
        raise HTTPException(status_code=400, detail="days must be between 1 and 31")
    include_google_bool = include_google.lower() in ("true", "1", "yes", "on")
    include_outlook_bool = include_outlook.lower() in ("true", "1", "yes", "on")

    svc = None
    if include_google_bool:
        try:
            _get_google_creds(SINGLE_USER_ID)
            svc = _google_calendar_service(SINGLE_USER_ID)
        except Exception as e:
            import logging
            logging.getLogger(__name__).info("Google Calendar not available (connect in Settings): %s", str(e)[:100])
            svc = None
    tz = _get_user_tz(SINGLE_USER_ID)

    start_utc, _ = _day_bounds_utc(SINGLE_USER_ID, start_day)
    end_utc = start_utc + timedelta(days=days)

    items: list[dict[str, Any]] = []
    if svc:
        page_token = None
        while True:
            resp = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=start_utc.isoformat(),
                    timeMax=end_utc.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            items.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    out: list[PlannedEventRangeOut] = []
    for ev in items:
        ev_id = ev.get("id", "") or f"ev_{secrets.token_hex(8)}"
        summary = ev.get("summary", "") or ""
        color_id = ev.get("colorId", "") or ""
        s = ev.get("start", {}) or {}
        e = ev.get("end", {}) or {}

        if "dateTime" in s and "dateTime" in e:
            try:
                sdt = datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
                edt = datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
        elif "date" in s and "date" in e:
            try:
                s_local = datetime.fromisoformat(s["date"]).replace(tzinfo=tz)
                e_local = datetime.fromisoformat(e["date"]).replace(tzinfo=tz)
                sdt = s_local.astimezone(timezone.utc)
                edt = e_local.astimezone(timezone.utc)
            except Exception:
                continue
        else:
            continue

        # Clip to range
        if edt <= start_utc or sdt >= end_utc:
            continue

        local_day = sdt.astimezone(tz).date().isoformat()
        out.append(
            PlannedEventRangeOut(
                id=ev_id,
                start_at=max(sdt, start_utc),
                end_at=min(edt, end_utc),
                summary=summary,
                color_id=color_id,
                suggested_category=_smart_categorize(summary, ""),
                day=local_day,
            )
        )

    # Merge Outlook planned events only when requested (Apple often syncs Outlook too)
    if include_outlook_bool:
        try:
            ms_row = _ms_token_row(SINGLE_USER_ID)
            if ms_row and ms_row["access_token"]:
                outlook_events = _fetch_outlook_events(start_utc, end_utc)
                for e in outlook_events:
                    local_day = e.start_at.astimezone(tz).date().isoformat()
                    out.append(
                        PlannedEventRangeOut(
                            id=e.id,
                            start_at=e.start_at,
                            end_at=e.end_at,
                            summary=e.summary,
                            color_id=e.color_id,
                            source="outlook",
                            source_calendar_name=e.source_calendar_name,
                            suggested_category=_smart_categorize(e.summary, ""),
                            day=local_day,
                        )
                    )
        except Exception:
            pass

    # Merge imported Apple Calendar ICS / EventKit planned events (with suggested_category for color)
    imported = _fetch_imported_planned(start_utc, end_utc)
    for e in imported:
        local_day = e.start_at.astimezone(tz).date().isoformat()
        out.append(
            PlannedEventRangeOut(
                id=e.id,
                start_at=e.start_at,
                end_at=e.end_at,
                summary=e.summary,
                color_id=e.color_id,
                source=e.source,
                source_calendar_name=e.source_calendar_name,
                suggested_category=e.suggested_category,
                day=local_day,
            )
        )

    # Hide planned events that already have a categorized log (by planned_event_id or overlap) so we show at most one block per event: the categorized one.
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT planned_event_id FROM time_entries
            WHERE user_id = ? AND planned_event_id IS NOT NULL AND planned_event_id != ''
              AND start_at < ? AND end_at > ?
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        categorized_planned_ids = {row["planned_event_id"] for row in cur.fetchall()}
        cur2 = conn.execute(
            """
            SELECT start_at, end_at FROM time_entries
            WHERE user_id = ? AND start_at < ? AND end_at > ?
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        logged_ranges: list[tuple[datetime, datetime]] = []
        for row in cur2.fetchall():
            try:
                s = datetime.fromisoformat(row["start_at"])
                e = datetime.fromisoformat(row["end_at"])
                if s.tzinfo is None:
                    s = s.replace(tzinfo=timezone.utc)
                if e.tzinfo is None:
                    e = e.replace(tzinfo=timezone.utc)
                logged_ranges.append((s, e))
            except Exception:
                pass

    def overlaps_any_logged(ev_start: datetime, ev_end: datetime) -> bool:
        for (s, e) in logged_ranges:
            if ev_start < e and ev_end > s:
                return True
        return False

    out = [
        e for e in out
        if e.id not in categorized_planned_ids and not overlaps_any_logged(e.start_at, e.end_at)
    ]
    return out


class TimeEntryRangeOut(TimeEntryOut):
    day: str  # local date YYYY-MM-DD (user timezone)


@app.get("/api/time_entries_range", response_model=list[TimeEntryRangeOut])
def api_time_entries_range(start_day: str | None = None, days: int = 7) -> list[TimeEntryRangeOut]:
    """
    Fetch TimeSense recorded entries (DB) for a local date range.
    Used to overlay records on top of the clean Google week plan.
    """
    if days < 1 or days > 31:
        raise HTTPException(status_code=400, detail="days must be between 1 and 31")

    tz = _get_user_tz(SINGLE_USER_ID)
    start_utc, _ = _day_bounds_utc(SINGLE_USER_ID, start_day)
    end_utc = start_utc + timedelta(days=days)

    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.id, te.start_at, te.end_at, te.title, te.category_id, te.tags_json, te.source, te.device,
                   c.name AS category_name, te.planned_event_id
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()

    # Only collapse duplicates that share planned_event_id (same Apple event logged twice). Do not collapse by (start_at, end_at, title) — that hid correct user logs.
    seen_planned: set[str] = set()
    deduped: list[sqlite3.Row] = []
    for r in rows:
        pid = (r["planned_event_id"] or "").strip() or None
        if pid:
            if pid in seen_planned:
                continue
            seen_planned.add(pid)
        deduped.append(r)

    out: list[TimeEntryRangeOut] = []
    for r in deduped:
        try:
            sdt = datetime.fromisoformat(r["start_at"])
            edt = datetime.fromisoformat(r["end_at"])
            if sdt.tzinfo is None:
                sdt = sdt.replace(tzinfo=timezone.utc)
            if edt.tzinfo is None:
                edt = edt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # Split entry across days so e.g. sleep 23:00–09:00 shows on both yesterday and today
        cursor = sdt
        while cursor < edt:
            day_start = cursor.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            day_end_utc = day_end.astimezone(timezone.utc)
            segment_end = min(edt, day_end_utc)
            if segment_end <= cursor:
                break
            local_day = cursor.astimezone(tz).date().isoformat()
            out.append(
                TimeEntryRangeOut(
                    id=r["id"],
                    start_at=cursor,
                    end_at=segment_end,
                    title=r["title"] or "",
                    category_id=r["category_id"],
                    category_name=r["category_name"] or "",
                    tags=json.loads(r["tags_json"] or "[]"),
                    source=r["source"],
                    device=r["device"],
                    day=local_day,
                )
            )
            cursor = segment_end
    return out


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


@app.post("/api/push/subscribe")
def api_push_subscribe(req: PushSubscriptionIn, request: Request) -> JSONResponse:
    sub_id = f"ps_{secrets.token_hex(10)}"
    ua = request.headers.get("user-agent", "")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions
              (id, user_id, endpoint, p256dh, auth, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sub_id,
                SINGLE_USER_ID,
                req.endpoint,
                req.keys.p256dh,
                req.keys.auth,
                ua,
                _utc_now().isoformat(),
            ),
        )
        conn.commit()
    return JSONResponse({"ok": True, "id": sub_id})


@app.post("/api/push/test")
def api_push_test() -> JSONResponse:
    result = _send_push(SINGLE_USER_ID, title="TimeSense", body="Push is working", url="/#quick")
    return JSONResponse(result)


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {"ok": True, "now": _utc_now().isoformat(), "db": str(DB_PATH)}


class UpdateTimezoneIn(BaseModel):
    timezone: str


@app.post("/api/user/timezone")
def api_user_timezone(req: UpdateTimezoneIn) -> JSONResponse:
    """
    Update user timezone. Used to align Google Calendar all-day events and day bounds with the user's actual locale.
    """
    try:
        ZoneInfo(req.timezone)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timezone")

    with _db() as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (req.timezone, SINGLE_USER_ID))
        conn.commit()
    return JSONResponse({"ok": True})


@app.get("/api/dev/env_status")
def api_dev_env_status() -> dict[str, Any]:
    """
    Dev-only helper: returns booleans, never secrets.
    Useful to confirm .env is being loaded.
    """
    return {
        "has_SESSION_SECRET": bool(os.getenv("SESSION_SECRET", "")),
        "has_GOOGLE_CLIENT_ID": bool(os.getenv("GOOGLE_CLIENT_ID", "")),
        "has_GOOGLE_CLIENT_SECRET": bool(os.getenv("GOOGLE_CLIENT_SECRET", "")),
        "google_redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", ""),
        "ai_builder_configured": bool(os.getenv("AI_BUILDER_TOKEN", "").strip()),
    }


# ─────────────────────────────────────────────────────────────
# Entry CRUD (delete/update)
# ─────────────────────────────────────────────────────────────

class UpdateEntryIn(BaseModel):
    title: str | None = None
    category_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


@app.delete("/api/time_entries/{entry_id}")
def api_delete_entry(entry_id: str) -> JSONResponse:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM time_entries WHERE id = ? AND user_id = ?",
            (entry_id, SINGLE_USER_ID),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Entry not found")
    return JSONResponse({"ok": True})


@app.patch("/api/time_entries/{entry_id}")
def api_update_entry(entry_id: str, req: UpdateEntryIn) -> JSONResponse:
    updates = []
    params = []
    if req.title is not None:
        updates.append("title = ?")
        params.append(req.title)
    if req.category_id is not None:
        updates.append("category_id = ?")
        params.append(req.category_id)
    if req.start_at is not None:
        updates.append("start_at = ?")
        params.append(req.start_at.isoformat())
    if req.end_at is not None:
        updates.append("end_at = ?")
        params.append(req.end_at.isoformat())
    if not updates:
        return JSONResponse({"ok": True, "updated": False})
    params.extend([entry_id, SINGLE_USER_ID])
    with _db() as conn:
        cur = conn.execute(
            f"UPDATE time_entries SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            params,
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Entry not found")
    return JSONResponse({"ok": True, "updated": True})


# ─────────────────────────────────────────────────────────────
# Analytics API
# ─────────────────────────────────────────────────────────────

# Smart categorization rules
SMART_CATEGORY_RULES = {
    # Keywords that suggest intimate/quality time (family, partner, close relationships)
    "intimate_keywords": ["kk", "partner", "date", "boyfriend", "girlfriend", "husband", "wife", "bae", "dinner with", "movie with", "mom", "mum", "dad", "family", "parent", "parents", "called mom", "call mom", "call dad", "phone mom", "quality time"],
    # Keywords that suggest entertainment/leisure
    "entertainment_keywords": ["shopping", "movie", "netflix", "game", "gaming", "tv", "show", "concert", "party"],
    # Keywords that suggest work (include "work" so typing "work" categorizes correctly)
    # "call" alone can be work; family/personal calls are matched by intimate_keywords first (mom, dad, etc.)
    "work_keywords": ["work", "meeting", "email", "project", "deadline", "client", "presentation", "report", "outlook", "sync", "standup", "1:1", "review", "conference call", "work call", "team call"],
    # Life essentials: meals, hygiene, household
    "life_essentials_keywords": ["lunch", "dinner", "breakfast", "meal", "eat", "shower", "bath", "cleaning", "laundry", "dishes", "cook", "groceries", "commute to store", "self-care", "hygiene", "brush teeth", "skincare"],
    # Keywords that suggest learning/study
    "learning_keywords": ["study", "learn", "course", "class", "reading", "book", "tutorial", "lecture"],
    # Keywords that suggest exercise
    "exercise_keywords": ["gym", "workout", "run", "running", "yoga", "swim", "bike", "hiking", "walk", "exercise", "lift", "row"],
    # Keywords that suggest wasted/unplanned time
    "wasted_keywords": ["wasted", "waste", "procrastinat", "unplanned", "scrolling", "doomscroll", "distract", "youtube", "tiktok", "reddit", "instagram", "ins time", "twitter", "x.com", "browse", "mindless", "drift", "lost time", "rabbit hole"],
}


def _smart_categorize(title: str, category_name: str) -> str:
    """
    Smart categorization based on title and existing category.
    Returns the most appropriate high-level category.
    """
    title_lower = (title or "").lower()
    cat_lower = (category_name or "").lower()
    
    # Check for wasted time keywords FIRST (high priority - user explicitly marked as wasted)
    for kw in SMART_CATEGORY_RULES["wasted_keywords"]:
        if kw in title_lower:
            return "Unplanned / Wasted"
    
    # Check for intimate time (high priority if partner mentioned)
    for kw in SMART_CATEGORY_RULES["intimate_keywords"]:
        if kw in title_lower:
            return "Intimate / Quality Time"
    
    # Map existing categories
    if "sleep" in cat_lower:
        return "Sleep"
    if "work" in cat_lower or "active" in cat_lower or "passive" in cat_lower:
        return "Work"
    if "learn" in cat_lower or "study" in cat_lower:
        return "Learning"
    if "exercise" in cat_lower or "workout" in cat_lower:
        return "Exercise"
    if "intim" in cat_lower or "quality" in cat_lower:
        return "Intimate / Quality Time"
    if "commute" in cat_lower:
        return "Commute"
    if "social" in cat_lower:
        return "Social"
    if "chore" in cat_lower:
        return "Chores"
    if "wasting" in cat_lower or "drift" in cat_lower or "unplanned" in cat_lower:
        return "Unplanned / Wasted"
    
    # Smart detection from title
    for kw in SMART_CATEGORY_RULES["work_keywords"]:
        if kw in title_lower:
            return "Work"
    for kw in SMART_CATEGORY_RULES["learning_keywords"]:
        if kw in title_lower:
            return "Learning"
    for kw in SMART_CATEGORY_RULES["exercise_keywords"]:
        if kw in title_lower:
            return "Exercise"
    for kw in SMART_CATEGORY_RULES["entertainment_keywords"]:
        if kw in title_lower:
            return "Entertainment"
    for kw in SMART_CATEGORY_RULES.get("life_essentials_keywords", []):
        if kw in title_lower:
            return "Life essentials"
    
    return "Other"


class GoalIn(BaseModel):
    title: str
    deadline: str  # YYYY-MM-DD


class GoalOut(BaseModel):
    id: str
    title: str
    deadline: str
    days_left: int
    created_at: str


@app.get("/api/analytics/week")
def api_analytics_week(
    start_day: str | None = None, lang: str = "en", model: str | None = None
) -> dict[str, Any]:
    """
    Get weekly analytics: time by category, smart breakdowns, insights.
    lang: en | zh for AI response language. model: optional AI model for insights (e.g. gpt-5, gemini-2.5-pro).
    """
    tz = _get_user_tz(SINGLE_USER_ID)
    if start_day:
        try:
            y, m, d = [int(x) for x in start_day.split("-")]
            base = datetime(y, m, d, tzinfo=tz)
        except:
            base = _utc_now().astimezone(tz)
    else:
        base = _utc_now().astimezone(tz)
    
    # Week starts Sunday 00:00 system time (Sunday–Saturday). weekday(): Mon=0 .. Sun=6.
    dow = base.weekday()
    days_since_sunday = (dow + 1) % 7
    sunday_start = base - timedelta(days=days_since_sunday)
    sunday_start = sunday_start.replace(hour=0, minute=0, second=0, microsecond=0)
    next_sunday = sunday_start + timedelta(days=7)
    
    start_utc = sunday_start.astimezone(timezone.utc)
    end_utc = next_sunday.astimezone(timezone.utc)
    
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.start_at, te.end_at, te.title, c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()
    
    # Aggregate by smart category
    category_minutes: dict[str, int] = {}
    total_logged_mins = 0
    
    for r in rows:
        try:
            s = datetime.fromisoformat(r["start_at"])
            e = datetime.fromisoformat(r["end_at"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            # Clip to week bounds
            s = max(s, start_utc)
            e = min(e, end_utc)
            mins = int((e - s).total_seconds() / 60)
            if mins <= 0:
                continue
            # Exclude whole-day events (>= 23 hours) from stats - they shouldn't count as 24h
            if mins >= 23 * 60:
                continue
            
            smart_cat = _smart_categorize(r["title"], r["category_name"])
            category_minutes[smart_cat] = category_minutes.get(smart_cat, 0) + mins
            total_logged_mins += mins
        except:
            continue
    
    # Calculate total available time (awake hours estimate: 16h/day * 7 days)
    total_week_mins = 7 * 24 * 60
    awake_mins = 7 * 16 * 60  # Assume 16 awake hours per day
    sleep_mins = category_minutes.get("Sleep", 0)
    
    # Build response
    breakdown = []
    for cat, mins in sorted(category_minutes.items(), key=lambda x: -x[1]):
        breakdown.append({
            "category": cat,
            "minutes": mins,
            "hours": round(mins / 60, 1),
            "percent": round(mins / max(1, total_logged_mins) * 100, 1),
        })
    
    # Calculate untracked time
    untracked_mins = max(0, total_week_mins - total_logged_mins)
    
    # Calculate productive vs wasted hours
    productive_categories = ["Work", "Learning", "Exercise", "Intimate / Quality Time"]
    wasted_categories = ["Unplanned / Wasted", "Unplanned wasting"]
    
    productive_mins = sum(category_minutes.get(cat, 0) for cat in productive_categories)
    wasted_mins = sum(category_minutes.get(cat, 0) for cat in wasted_categories)
    
    # Insights: use AI (GPT-5) when AI_BUILDER_TOKEN is set for detailed, actionable suggestions
    category_breakdown_str = "\n".join(
        f"- {b['category']}: {b['hours']}h ({b['percent']}%)" for b in breakdown
    )
    detail_lines = []
    for r in rows[:80]:
        try:
            s = datetime.fromisoformat(r["start_at"]).astimezone(tz)
            e = datetime.fromisoformat(r["end_at"]).astimezone(tz)
            day = s.strftime("%a %d")
            seg = f"{day} {s.strftime('%H:%M')}-{e.strftime('%H:%M')} {r['category_name'] or '?'}: {r['title'] or '(no title)'}"
            detail_lines.append(seg)
        except Exception:
            continue
    detail_log = "\n".join(detail_lines)
    insights, insights_error = _generate_ai_insights(
        detail_log, category_breakdown_str, 7, lang=lang, model=model
    )
    if not insights:
        insights = _generate_insights(category_minutes, total_logged_mins, 7)
    
    return {
        "week_start": sunday_start.date().isoformat(),
        "week_end": (next_sunday - timedelta(days=1)).date().isoformat(),
        "total_logged_minutes": total_logged_mins,
        "total_logged_hours": round(total_logged_mins / 60, 1),
        "untracked_minutes": untracked_mins,
        "untracked_hours": round(untracked_mins / 60, 1),
        "productive_hours": round(productive_mins / 60, 1),
        "wasted_hours": round(wasted_mins / 60, 1),
        "breakdown": breakdown,
        "coverage_percent": round(total_logged_mins / total_week_mins * 100, 1),
        "insights": insights,
        "insights_error": insights_error,
    }


@app.get("/api/goals")
def api_get_goals() -> list[GoalOut]:
    """Get user goals with countdown."""
    # For MVP, store goals in a simple table. First ensure it exists.
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              title TEXT NOT NULL,
              deadline TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        
        cur = conn.execute(
            "SELECT id, title, deadline, created_at FROM goals WHERE user_id = ? ORDER BY deadline ASC",
            (SINGLE_USER_ID,),
        )
        rows = cur.fetchall()
    
    today = _utc_now().date()
    out = []
    for r in rows:
        try:
            deadline = datetime.fromisoformat(r["deadline"]).date()
            days_left = (deadline - today).days
        except:
            days_left = 0
        out.append(GoalOut(
            id=r["id"],
            title=r["title"],
            deadline=r["deadline"],
            days_left=days_left,
            created_at=r["created_at"],
        ))
    return out


@app.post("/api/goals")
def api_add_goal(req: GoalIn) -> GoalOut:
    """Add a new goal."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              title TEXT NOT NULL,
              deadline TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        goal_id = f"goal_{secrets.token_hex(8)}"
        now = _utc_now().isoformat()
        conn.execute(
            "INSERT INTO goals (id, user_id, title, deadline, created_at) VALUES (?, ?, ?, ?, ?)",
            (goal_id, SINGLE_USER_ID, req.title, req.deadline, now),
        )
        conn.commit()
    
    today = _utc_now().date()
    try:
        deadline = datetime.fromisoformat(req.deadline).date()
        days_left = (deadline - today).days
    except:
        days_left = 0
    
    return GoalOut(id=goal_id, title=req.title, deadline=req.deadline, days_left=days_left, created_at=now)


@app.delete("/api/goals/{goal_id}")
def api_delete_goal(goal_id: str) -> JSONResponse:
    with _db() as conn:
        conn.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, SINGLE_USER_ID))
        conn.commit()
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────
# Day notes (diary) API – for comments and later analysis with activity
# ─────────────────────────────────────────────────────────────

def _ensure_day_notes_table() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS day_notes (
              user_id TEXT NOT NULL,
              day TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, day)
            )
            """
        )
        conn.commit()


@app.get("/api/day_notes")
def api_get_day_note(day: str) -> dict[str, Any]:
    """Get diary/comment note for a given day (YYYY-MM-DD)."""
    _ensure_day_notes_table()
    with _db() as conn:
        cur = conn.execute(
            "SELECT note, updated_at FROM day_notes WHERE user_id = ? AND day = ?",
            (SINGLE_USER_ID, day),
        )
        row = cur.fetchone()
    if not row:
        return {"day": day, "note": "", "updated_at": None}
    return {"day": day, "note": row["note"] or "", "updated_at": row["updated_at"]}


class DayNoteIn(BaseModel):
    note: str


@app.post("/api/day_notes/{day}")
def api_set_day_note(day: str, req: DayNoteIn) -> JSONResponse:
    """Set diary/comment note for a day (YYYY-MM-DD)."""
    _ensure_day_notes_table()
    now = _utc_now().isoformat()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO day_notes (user_id, day, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at
            """,
            (SINGLE_USER_ID, day, req.note or "", now),
        )
        conn.commit()
    return JSONResponse({"ok": True, "day": day, "updated_at": now})


def _extract_content_from_message(msg: dict) -> str | None:
    """Extract text from OpenAI-style message; content may be str or list of parts (multimodal)."""
    raw = msg.get("content")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, list):
        parts = []
        for part in raw:
            if isinstance(part, dict):
                t = part.get("text") or part.get("content") or part.get("value")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
                elif isinstance(t, dict) and isinstance(t.get("content"), str) and t["content"].strip():
                    parts.append(t["content"].strip())
                elif not t and part.get("type") == "text":
                    for k, v in part.items():
                        if k != "type" and isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                            break
            elif isinstance(part, str) and part.strip():
                parts.append(part.strip())
        return " ".join(parts) if parts else None
    return None


# Keys in API responses that are metadata, not message content (so we never return e.g. completion id as text).
_RESPONSE_METADATA_KEYS = frozenset({"id", "object", "model", "system_fingerprint", "created", "usage"})


# Keywords that suggest a string is the reflection reply (not a prompt or system message).
_REFLECTION_KEYWORDS = ("suggestion", "tomorrow", "went well", "pattern", "concrete", "paragraph", "stands out", "tension", "kind", "supportive")


def _extract_content_from_orchestrator_trace(data: dict, prefer_reflection: bool = False) -> str | None:
    """AI Builder Space may put the actual reply in orchestrator_trace when message.content is empty.
    When prefer_reflection=True, pick the candidate that best matches reflection keywords (for day analysis)."""
    trace = data.get("orchestrator_trace")
    if not trace or not isinstance(trace, dict):
        return None
    candidates: list[str] = []

    def collect(o: Any) -> None:
        if isinstance(o, str) and len(o.strip()) > 50:
            candidates.append(o.strip())
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in _RESPONSE_METADATA_KEYS:
                    continue
                collect(v)
        elif isinstance(o, list):
            for x in o:
                collect(x)

    collect(trace)
    if not candidates:
        return None
    if prefer_reflection:
        lower = [c.lower() for c in candidates]
        scored = [(c, sum(1 for kw in _REFLECTION_KEYWORDS if kw in c.lower())) for c in candidates]
        best = max(scored, key=lambda x: x[1])
        if best[1] > 0:
            return best[0]
        # Fall back to longest if no keyword match (avoid returning prompts)
        return max(candidates, key=len)
    return max(candidates, key=len)


def _extract_longest_string_from_response(data: dict) -> str | None:
    """Fallback: find the longest substantial string in the response (likely the model reply). Skips metadata (id, etc.)."""
    candidates: list[str] = []

    def collect(o: Any, skip_metadata: bool = False) -> None:
        if isinstance(o, str) and len(o.strip()) > 30:
            candidates.append(o.strip())
        elif isinstance(o, dict):
            for k, v in o.items():
                if skip_metadata and k in _RESPONSE_METADATA_KEYS:
                    continue
                collect(v, skip_metadata=True)
        elif isinstance(o, list):
            for x in o:
                collect(x, skip_metadata=True)

    collect(data, skip_metadata=True)
    return max(candidates, key=len) if candidates else None


def _ai_builder_chat(
    model: str,
    messages: list[dict],
    max_tokens: int = 1000,
    temperature: float = 1.0,
    tool_choice: str | None = None,
    timeout: int = 90,
    debug: bool = False,
    strict_content_only: bool = False,
    prefer_reflection_in_trace: bool = False,
) -> tuple[str | None, str | None]:
    """Call AI Builders chat API. prefer_reflection_in_trace=True: when using trace, pick text that looks like the reflection reply."""
    import logging
    log = logging.getLogger(__name__)
    token = os.getenv("AI_BUILDER_TOKEN", "").strip()
    base_url = os.getenv("AI_BUILDER_BASE_URL", "https://space.ai-builders.com/backend/v1").rstrip("/")
    if not token:
        log.warning("AI Builders: AI_BUILDER_TOKEN not set. Add it to .env next to main.py and restart the backend.")
        return None, "AI_BUILDER_TOKEN not set. Add it to .env in the project root and restart the backend."
    url = f"{base_url}/chat/completions"
    if debug:
        url += "?debug=true"
    log.info("AI Builders: calling %s model=%s timeout=%s", url, model, timeout)
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if r.status_code != 200:
            err_body = r.text
            try:
                err_json = r.json()
                err_body = err_json.get("detail") or err_json.get("error", {}).get("message") or err_body
            except Exception:
                pass
            log.warning("AI Builders error %s: %s", r.status_code, err_body[:300])
            return None, f"AI API error {r.status_code}: {err_body[:200]}"
        data = r.json()
        # Some APIs put text at top level
        content = None
        if isinstance(data.get("output"), str) and data["output"].strip():
            content = data["output"].strip()
        if not content and isinstance(data.get("text"), str) and data["text"].strip():
            content = data["text"].strip()
        if not content and isinstance(data.get("generated_text"), str) and data["generated_text"].strip():
            content = data["generated_text"].strip()
        choices = data.get("choices") or []
        if not choices and not content:
            return None, "AI API returned no choices; response may be incomplete or rate-limited."
        if not content and choices:
            choice = choices[0]
            msg = choice.get("message") or {}
            content = _extract_content_from_message(msg)
            if not content and isinstance(choice.get("text"), str) and choice["text"].strip():
                content = choice["text"].strip()
            if not content and isinstance(msg.get("content"), str) and msg["content"].strip():
                content = msg["content"].strip()
            if not content:
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str) and delta["content"].strip():
                    content = delta["content"].strip()
        if not content and not strict_content_only:
            content = _extract_content_from_orchestrator_trace(data, prefer_reflection=prefer_reflection_in_trace)
        if not content and not strict_content_only:
            content = _extract_longest_string_from_response(data)
        if not content:
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                return None, "AI agent used tools instead of direct text. Try a non-agent model or enable text-only in settings."
            debug_path = Path(__file__).parent / "DEBUG_AI_EMPTY_RESPONSE.json"
            try:
                sanitized = json.dumps(
                    {
                        "model": model,
                        "response_keys": list(data.keys()),
                        "choices_len": len(data.get("choices") or []),
                        "first_choice_keys": list(choice.keys()) if choices else [],
                        "message_keys": list(msg.keys()),
                        "message_content_type": type(msg.get("content")).__name__ if msg.get("content") is not None else "None",
                        "message_content_sample": repr(msg.get("content"))[:2000] if msg.get("content") is not None else None,
                    },
                    indent=2,
                )
                debug_path.write_text(sanitized, encoding="utf-8")
                log.warning("AI Builders 200 but empty content: wrote response shape to %s", debug_path)
            except Exception as e:
                log.warning("AI Builders 200 but empty content: could not write debug file: %s", e)
            return None, "AI returned an empty response. [Backend updated] Try gpt-5 or gemini-2.5-pro in the Model dropdown. Check DEBUG_AI_EMPTY_RESPONSE.json in the project root for the response shape."
        return content, None
    except requests.exceptions.Timeout:
        return None, "AI API timeout. Try again."
    except requests.exceptions.RequestException as e:
        return None, f"AI API connection error: {str(e)[:150]}"
    except Exception as e:
        return None, f"AI error: {str(e)[:150]}"


# Fallback when primary returns empty; use model that reliably fills message.content.
DAY_ANALYSIS_FALLBACK_MODEL = "gemini-2.5-pro"
# gpt-5 and similar can take 60–120s for a reflection; avoid timeout.
DAY_ANALYSIS_TIMEOUT = 120


def _ai_day_analysis(day: str, note: str, activity_summary: str, lang: str = "en", model: str | None = None) -> tuple[str | None, str | None]:
    """Use AI Builders. Default gemini-3-flash-preview (quickest). Single user message; only message.content used (no trace)."""
    model = (model or "").strip() or os.getenv("AI_BUILDER_ANALYSIS_MODEL", "gemini-3-flash-preview")
    lang_instruction = "Respond only in 简体中文 (Simplified Chinese)." if (lang or "").lower() == "zh" else "Respond only in English."
    prompt = f"""You are a supportive coach. Analyze this person's day and give a short, actionable reflection.

Date: {day}

Their note/diary for the day:
{note or "(no note)"}

Their logged activity (category, title, time):
{activity_summary or "(no activity logged)"}

Respond in 2–4 short paragraphs: (1) what went well or what stands out, (2) any pattern or tension between how they spent time and their note, (3) one concrete suggestion for tomorrow. Be concise and kind. {lang_instruction}"""
    messages = [{"role": "user", "content": prompt}]
    # Allow trace extraction so we get a reply when message.content is empty; prefer text that looks like the reflection.
    content, err = _ai_builder_chat(
        model, messages, max_tokens=1000, timeout=DAY_ANALYSIS_TIMEOUT,
        strict_content_only=False, prefer_reflection_in_trace=True, debug=True,
    )
    if content and content.strip():
        return content, None
    if DAY_ANALYSIS_FALLBACK_MODEL and DAY_ANALYSIS_FALLBACK_MODEL != model:
        content, err = _ai_builder_chat(
            DAY_ANALYSIS_FALLBACK_MODEL, messages, max_tokens=1000, timeout=DAY_ANALYSIS_TIMEOUT,
            strict_content_only=False, prefer_reflection_in_trace=True, debug=True,
        )
        if content and content.strip():
            return content, None
    return None, err or "AI returned no text. Try gpt-5 or gemini-2.5-pro in the Model dropdown."


@app.get("/api/ai/day_analysis")
def api_ai_day_analysis(day: str, lang: str = "en", model: str | None = None, debug: bool = False) -> dict[str, Any]:
    """Get AI analysis combining the day's note and logged activity. model: optional (e.g. gpt-5). debug: include hint for empty responses."""
    _ensure_day_notes_table()
    tz = _get_user_tz(SINGLE_USER_ID)
    start_utc, end_utc = _day_bounds_utc(SINGLE_USER_ID, day)

    # Day note
    with _db() as conn:
        cur = conn.execute(
            "SELECT note FROM day_notes WHERE user_id = ? AND day = ?",
            (SINGLE_USER_ID, day),
        )
        row = cur.fetchone()
    note = (row["note"] or "").strip() if row else ""

    # Time entries for this day
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.start_at, te.end_at, te.title, c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()

    lines: list[str] = []
    for r in rows:
        try:
            s = datetime.fromisoformat(r["start_at"]).astimezone(tz)
            e = datetime.fromisoformat(r["end_at"]).astimezone(tz)
            mins = int((e - s).total_seconds() / 60)
            cat = r["category_name"] or "?"
            title = (r["title"] or "").strip()
            seg = f"- {s.strftime('%H:%M')}–{e.strftime('%H:%M')} ({mins}m) {cat}"
            if title:
                seg += f": {title}"
            lines.append(seg)
        except Exception:
            continue
    activity_summary = "\n".join(lines) if lines else ""

    analysis, err = _ai_day_analysis(day, note, activity_summary, lang=lang, model=model)
    out = {
        "day": day,
        "note": note,
        "activity_summary": activity_summary,
        "analysis": analysis if analysis else (f"AI analysis unavailable. {err}" if err else "No response from AI."),
        "day_analysis_version": "v2",
        "error": err,
    }
    if debug and err and "empty" in (err or "").lower():
        out["debug_hint"] = "Check the terminal where the backend is running for 'AI Builders 200 but empty content', or see DEBUG_AI_EMPTY_RESPONSE.json in the project root."
    return out


# ─────────────────────────────────────────────────────────────
# Weekly Targets API
# ─────────────────────────────────────────────────────────────

class WeeklyTargetIn(BaseModel):
    category: str
    target_type: str  # "hours_per_day", "hours_per_week", "min_hours", "max_hours"
    target_value: float
    start_date: str | None = None
    end_date: str | None = None


class WeeklyTargetOut(BaseModel):
    id: str
    category: str
    target_type: str
    target_value: float
    start_date: str | None = None
    end_date: str | None = None
    created_at: str


def _ensure_weekly_targets_table() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_targets (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              category TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_value REAL NOT NULL,
              start_date TEXT,
              end_date TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.get("/api/targets", response_model=list[WeeklyTargetOut])
def api_get_targets() -> list[WeeklyTargetOut]:
    _ensure_weekly_targets_table()
    with _db() as conn:
        cur = conn.execute(
            "SELECT id, category, target_type, target_value, start_date, end_date, created_at FROM weekly_targets WHERE user_id = ?",
            (SINGLE_USER_ID,),
        )
        rows = cur.fetchall()
    return [
        WeeklyTargetOut(
            id=r["id"],
            category=r["category"],
            target_type=r["target_type"],
            target_value=r["target_value"],
            start_date=r["start_date"],
            end_date=r["end_date"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@app.post("/api/targets", response_model=WeeklyTargetOut)
def api_add_target(req: WeeklyTargetIn) -> WeeklyTargetOut:
    _ensure_weekly_targets_table()
    target_id = f"target_{secrets.token_hex(8)}"
    now = _utc_now().isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO weekly_targets (id, user_id, category, target_type, target_value, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (target_id, SINGLE_USER_ID, req.category, req.target_type, req.target_value, req.start_date, req.end_date, now),
        )
        conn.commit()
    return WeeklyTargetOut(
        id=target_id,
        category=req.category,
        target_type=req.target_type,
        target_value=req.target_value,
        start_date=req.start_date,
        end_date=req.end_date,
        created_at=now,
    )


@app.delete("/api/targets/{target_id}")
def api_delete_target(target_id: str) -> JSONResponse:
    with _db() as conn:
        conn.execute("DELETE FROM weekly_targets WHERE id = ? AND user_id = ?", (target_id, SINGLE_USER_ID))
        conn.commit()
    return JSONResponse({"ok": True})


class TargetProgressItem(BaseModel):
    category: str
    target_type: str
    target_value: float
    actual_hours: float
    expected_hours: float
    percent: float
    status: str  # "on_track", "behind", "ahead"
    # For hours_per_day: how many days in the range met the target (e.g. 2/7)
    days_met: int | None = None
    days_total: int | None = None


class TargetProgressOut(BaseModel):
    start_date: str
    end_date: str
    progress: list[TargetProgressItem]


@app.get("/api/targets/progress", response_model=TargetProgressOut)
def api_targets_progress(start_date: str, end_date: str) -> TargetProgressOut:
    """
    Calculate progress for each target given a date range.
    """
    _ensure_weekly_targets_table()
    tz = _get_user_tz(SINGLE_USER_ID)
    
    # Parse dates
    try:
        start_local = datetime.fromisoformat(start_date).replace(tzinfo=tz)
        end_local = datetime.fromisoformat(end_date).replace(tzinfo=tz) + timedelta(days=1)  # Include end date
    except:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    days_in_range = max(1, (end_local.date() - start_local.date()).days)
    
    # Get targets
    with _db() as conn:
        cur = conn.execute(
            "SELECT category, target_type, target_value FROM weekly_targets WHERE user_id = ?",
            (SINGLE_USER_ID,),
        )
        targets = [dict(r) for r in cur.fetchall()]
    
    if not targets:
        return TargetProgressOut(start_date=start_date, end_date=end_date, progress=[])
    
    # Get time entries in range and aggregate by smart category
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.start_at, te.end_at, te.title, c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()
    
    category_minutes: dict[str, int] = {}
    # Per-day minutes per (smart_cat, date) for hours_per_day targets
    category_day_minutes: dict[str, dict] = {}
    for r in rows:
        try:
            s = datetime.fromisoformat(r["start_at"])
            e = datetime.fromisoformat(r["end_at"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            s = max(s, start_utc)
            e = min(e, end_utc)
            mins = int((e - s).total_seconds() / 60)
            if mins <= 0:
                continue
            # Exclude whole-day events (>= 23 hours) from targets/stats - they shouldn't count as 24h
            if mins >= 23 * 60:
                continue
            
            smart_cat = _smart_categorize(r["title"], r["category_name"])
            category_minutes[smart_cat] = category_minutes.get(smart_cat, 0) + mins
            # Accumulate per-day: split event by day so minutes count on each day they fall in
            if smart_cat not in category_day_minutes:
                category_day_minutes[smart_cat] = {}
            cur = s
            while cur < e:
                # End of current day (start of next day) in same tz
                next_day = cur.date() + timedelta(days=1)
                day_end_dt = datetime.combine(next_day, datetime.min.time()).replace(tzinfo=cur.tzinfo)
                day_end = min(e, day_end_dt)
                day_mins = int((day_end - cur).total_seconds() / 60)
                day_key = cur.date()
                category_day_minutes[smart_cat][day_key] = category_day_minutes[smart_cat].get(day_key, 0) + day_mins
                cur = day_end
        except Exception:
            continue
    
    # Normalize category for lookup (analytics uses smart categories e.g. "Work" not "Work (active)")
    def _target_actual_mins(category: str) -> int:
        if category in ("Work (active)", "Work (passive)"):
            return category_minutes.get("Work", 0)
        return category_minutes.get(category, 0)

    def _target_day_minutes(category: str) -> dict:
        """Return dict of date -> minutes for this category (for hours_per_day)."""
        if category in ("Work (active)", "Work (passive)"):
            return category_day_minutes.get("Work", {})
        return category_day_minutes.get(category, {})

    progress: list[TargetProgressItem] = []
    start_date_only = start_local.date()
    end_date_only = end_local.date() - timedelta(days=1)  # end_date is exclusive in range
    days_list = [start_date_only + timedelta(days=i) for i in range(days_in_range)]
    days_total = len(days_list)

    for t in targets:
        cat = t["category"]
        target_type = t["target_type"]
        target_value = t["target_value"]
        actual_mins = _target_actual_mins(cat)
        actual_hours = round(actual_mins / 60, 1)
        
        # For hours_per_day: count how many days met the target (e.g. 2/7)
        days_met = None
        if target_type == "hours_per_day":
            day_mins = _target_day_minutes(cat)
            target_mins_per_day = target_value * 60
            days_met = sum(1 for d in days_list if day_mins.get(d, 0) >= target_mins_per_day)
        
        # Calculate expected hours based on target type
        if target_type == "hours_per_day":
            expected_hours = round(target_value * days_in_range, 1)
        elif target_type == "hours_per_week":
            expected_hours = round(target_value * (days_in_range / 7), 1)
        elif target_type == "min_hours":
            expected_hours = target_value
        elif target_type == "max_hours":
            expected_hours = target_value
        else:
            expected_hours = target_value
        
        # Calculate percent and status
        if target_type == "hours_per_day" and days_total > 0:
            percent = round((days_met / days_total) * 100, 1) if days_met is not None else 0.0
        elif expected_hours > 0:
            percent = round((actual_hours / expected_hours) * 100, 1)
        else:
            percent = 100.0 if actual_hours > 0 else 0.0
        
        if target_type == "max_hours":
            # For max hours, being under is good
            status = "ahead" if actual_hours <= expected_hours else "behind"
        else:
            # For other types, meeting or exceeding is good
            if percent >= 90:
                status = "on_track"
            elif percent >= 50:
                status = "behind"
            else:
                status = "behind"
            if percent >= 100:
                status = "ahead"
        
        progress.append(TargetProgressItem(
            category=cat,
            target_type=target_type,
            target_value=target_value,
            actual_hours=actual_hours,
            expected_hours=expected_hours,
            percent=percent,
            status=status,
            days_met=days_met if target_type == "hours_per_day" else None,
            days_total=days_total if target_type == "hours_per_day" else None,
        ))
    
    return TargetProgressOut(start_date=start_date, end_date=end_date, progress=progress)


# ─────────────────────────────────────────────────────────────
# Uncategorized Events API
# ─────────────────────────────────────────────────────────────

class UncategorizedEventOut(BaseModel):
    id: str
    title: str
    start_at: datetime
    end_at: datetime
    source: str
    calendar: str


@app.get("/api/events/uncategorized", response_model=list[UncategorizedEventOut])
def api_uncategorized_events(limit: int = 20) -> list[UncategorizedEventOut]:
    """
    Return planned events that don't have a corresponding time entry logged.
    These are events from Apple Calendar that need to be categorized.
    """
    now_utc = _utc_now()
    # Look at past 7 days of planned events
    start_utc = now_utc - timedelta(days=7)
    
    with _db() as conn:
        # Get planned events in range
        cur = conn.execute(
            """
            SELECT id, title, start_at, end_at, source, source_calendar_name
            FROM planned_events_imported
            WHERE user_id = ?
              AND start_at < ?
              AND end_at > ?
              AND is_all_day = 0
            ORDER BY start_at DESC
            """,
            (SINGLE_USER_ID, now_utc.isoformat(), start_utc.isoformat()),
        )
        planned = cur.fetchall()
        
        # Get logged time entries in same range
        cur2 = conn.execute(
            """
            SELECT start_at, end_at
            FROM time_entries
            WHERE user_id = ?
              AND start_at < ?
              AND end_at > ?
            """,
            (SINGLE_USER_ID, now_utc.isoformat(), start_utc.isoformat()),
        )
        entries = cur2.fetchall()
    
    # Build list of logged time ranges
    logged_ranges: list[tuple[datetime, datetime]] = []
    for e in entries:
        try:
            s = datetime.fromisoformat(e["start_at"])
            t = datetime.fromisoformat(e["end_at"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            logged_ranges.append((s, t))
        except:
            continue
    
    # Find planned events not covered by entries
    uncategorized: list[UncategorizedEventOut] = []
    for p in planned:
        try:
            ps = datetime.fromisoformat(p["start_at"])
            pe = datetime.fromisoformat(p["end_at"])
            if ps.tzinfo is None:
                ps = ps.replace(tzinfo=timezone.utc)
            if pe.tzinfo is None:
                pe = pe.replace(tzinfo=timezone.utc)
        except:
            continue
        
        # Check if this event overlaps significantly with any logged entry
        covered = False
        for ls, le in logged_ranges:
            # Calculate overlap
            overlap_start = max(ps, ls)
            overlap_end = min(pe, le)
            if overlap_end > overlap_start:
                overlap_mins = (overlap_end - overlap_start).total_seconds() / 60
                event_mins = (pe - ps).total_seconds() / 60
                if event_mins > 0 and (overlap_mins / event_mins) >= 0.5:
                    covered = True
                    break
        
        if not covered:
            uncategorized.append(UncategorizedEventOut(
                id=p["id"],
                title=p["title"] or "Untitled",
                start_at=ps,
                end_at=pe,
                source=p["source"] or "apple_eventkit",
                calendar=p["source_calendar_name"] or "Calendar",
            ))
        
        if len(uncategorized) >= limit:
            break
    
    return uncategorized


# Canonical categorization sheet: same list used for AI, review panel, and analytics.
CANONICAL_CATEGORIES = [
    "Work (active)", "Work (passive)", "Learning", "Exercise", "Life essentials",
    "Sleep", "Social", "Chores", "Entertainment", "Commute",
    "Intimate / Quality Time", "Unplanned / Wasted", "Other",
]


def _ai_categorize_title(title: str) -> str | None:
    """Call AI Builders (supermind-agent-v1) to classify activity title."""
    model = os.getenv("AI_BUILDER_CATEGORIZE_MODEL", "supermind-agent-v1")
    category_list = ", ".join(CANONICAL_CATEGORIES)
    prompt = f"""Classify this activity title into exactly one category. Reply with only the category name, nothing else.
Categories: {category_list}
Title: "{title}"
Rules: Use "Intimate / Quality Time" for family/personal (e.g. called Mom, KK, partner, date). Use "Work (active)" for focused work AND meetings/calls (default meetings to active). Use "Work (passive)" only for passive activities like listening to podcasts or background reading. Use "Life essentials" for meals, shower, cleaning."""
    content, _ = _ai_builder_chat(model, [{"role": "user", "content": prompt}], max_tokens=50, temperature=0.2)
    if not content:
        return None
    content = content.split("\n")[0].strip()
    if content in CANONICAL_CATEGORIES:
        return content
    for cat in CANONICAL_CATEGORIES:
        if cat.lower() in content.lower() or content.lower() in cat.lower():
            return cat
    return None


def _resolve_category_id_from_name(conn: sqlite3.Connection, smart_cat: str) -> str:
    """Resolve a category name (e.g. from AI) to category_id for SINGLE_USER_ID."""
    cur = conn.execute(
        "SELECT id, name FROM categories WHERE user_id = ?",
        (SINGLE_USER_ID,),
    )
    cats = {r["name"].lower(): r["id"] for r in cur.fetchall()}
    if not cats:
        return ""
    cat_id = ""
    if smart_cat == "Work":
        cat_id = cats.get("work (active)", cats.get("work (passive)", ""))
    if not cat_id:
        for name, cid in cats.items():
            if smart_cat.lower() in name.lower() or name.lower() in smart_cat.lower():
                cat_id = cid
                break
    if not cat_id and ("life" in smart_cat.lower() or "essential" in smart_cat.lower()):
        for name, cid in cats.items():
            if "life" in name.lower() or "essential" in name.lower():
                cat_id = cid
                break
    if not cat_id and ("intimate" in smart_cat.lower() or "quality time" in smart_cat.lower()):
        for name, cid in cats.items():
            if "intim" in name.lower() or "quality" in name.lower():
                cat_id = cid
                break
    if not cat_id:
        cat_id = cats.get("other", list(cats.values())[0])
    return cat_id or ""


def _get_or_create_category_by_name(conn: sqlite3.Connection, name: str) -> str:
    """Get category_id by exact or canonical name; create category if missing. Returns "" if name is empty."""
    name = (name or "").strip()
    if not name:
        return ""
    cur = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?)",
        (SINGLE_USER_ID, name),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    # If this is a canonical name, create it so review panel choices stay aligned with the sheet
    if name in CANONICAL_CATEGORIES:
        pass  # fall through to create
    else:
        # Resolve to existing category if possible (e.g. "Quality Time" -> "Intimate / Quality Time")
        cat_id = _resolve_category_id_from_name(conn, name)
        if cat_id:
            return cat_id
    # Create new category with this name
    cid = f"cat_{secrets.token_hex(8)}"
    has_color = _categories_include_color(conn)
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) FROM categories WHERE user_id = ?",
        (SINGLE_USER_ID,),
    ).fetchone()[0]
    if has_color:
        conn.execute(
            "INSERT INTO categories (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at, color) VALUES (?, ?, ?, 0, 1, ?, ?, NULL)",
            (cid, SINGLE_USER_ID, name, int(max_order) + 1, _utc_now().isoformat()),
        )
    else:
        conn.execute(
            "INSERT INTO categories (id, user_id, name, is_prompt_choice, is_writable, sort_order, created_at) VALUES (?, ?, ?, 0, 1, ?, ?)",
            (cid, SINGLE_USER_ID, name, int(max_order) + 1, _utc_now().isoformat()),
        )
    return cid


def _ensure_imported_event_categorized(planned_id: str, title: str, start_at: datetime, end_at: datetime) -> bool:
    """If no time_entry exists for this planned event, create one (AI categorize) and return True. Else return False."""
    with _db() as conn:
        cur = conn.execute(
            "SELECT 1 FROM time_entries WHERE user_id = ? AND planned_event_id = ? LIMIT 1",
            (SINGLE_USER_ID, planned_id),
        )
        if cur.fetchone():
            return False
        smart_cat = _ai_categorize_title(title or "Event")
        if smart_cat is None:
            smart_cat = _smart_categorize(title or "Event", "")
        cat_id = _resolve_category_id_from_name(conn, smart_cat)
        if not cat_id:
            cat_id = _resolve_category_id_from_name(conn, "Other")
        if not cat_id:
            return False
        entry_id = f"te_{secrets.token_hex(10)}"
        start_str = start_at.isoformat() if hasattr(start_at, "isoformat") else str(start_at)
        end_str = end_at.isoformat() if hasattr(end_at, "isoformat") else str(end_at)
        conn.execute(
            """
            INSERT INTO time_entries
              (id, user_id, start_at, end_at, title, category_id, tags_json, source, device, created_at, planned_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                SINGLE_USER_ID,
                start_str,
                end_str,
                (title or "").strip(),
                cat_id,
                "[]",
                "calendar_import",
                "web",
                _utc_now().isoformat(),
                planned_id,
            ),
        )
        conn.commit()
    return True


class AutoCategorizeIn(BaseModel):
    title: str
    use_ai: bool = False  # When true, use AI Builders to classify (smart categorization)


class AutoCategorizeOut(BaseModel):
    category: str
    category_id: str
    confidence: float


@app.post("/api/auto_categorize", response_model=AutoCategorizeOut)
def api_auto_categorize(req: AutoCategorizeIn) -> AutoCategorizeOut:
    """
    Auto-categorize a title. Uses AI (AI Builders GPT-5) when use_ai=True or when AI_BUILDER_TOKEN is set; else rule-based.
    """
    smart_cat = None
    if req.use_ai or os.getenv("AI_BUILDER_TOKEN", "").strip():
        smart_cat = _ai_categorize_title(req.title)
    if smart_cat is None:
        smart_cat = _smart_categorize(req.title, "")
    
    # Find the category_id for this smart category
    with _db() as conn:
        cur = conn.execute(
            "SELECT id, name FROM categories WHERE user_id = ?",
            (SINGLE_USER_ID,),
        )
        cats = {r["name"].lower(): r["id"] for r in cur.fetchall()}
    
    # Try to match smart category to actual category (prefer "Work (active)" for work)
    cat_id = ""
    if smart_cat == "Work":
        cat_id = cats.get("work (active)", cats.get("work (passive)", ""))
    if not cat_id:
        for name, cid in cats.items():
            if smart_cat.lower() in name.lower() or name.lower() in smart_cat.lower():
                cat_id = cid
                break
    if not cat_id:
        if smart_cat == "Life essentials":
            for name, cid in cats.items():
                if "life" in name.lower() or "essential" in name.lower():
                    cat_id = cid
                    break
    if not cat_id:
        # Intimate / Quality Time (AI may return this)
        if "intimate" in smart_cat.lower() or "quality time" in smart_cat.lower():
            for name, cid in cats.items():
                if "intim" in name.lower() or "quality" in name.lower():
                    cat_id = cid
                    break
    if not cat_id:
        cat_id = cats.get("other", list(cats.values())[0] if cats else "")
    
    return AutoCategorizeOut(
        category=smart_cat,
        category_id=cat_id,
        confidence=0.8 if cat_id else 0.3,
    )


class CategorizeEventIn(BaseModel):
    event_id: str
    category_id: str | None = None
    category_name: str | None = None
    title: str | None = None


@app.post("/api/events/categorize")
def api_categorize_event(req: CategorizeEventIn) -> JSONResponse:
    """
    Categorize a planned event by creating a time entry for it.
    Provide category_id or category_name (canonical name from categorization sheet).
    If this event already has a time entry (same planned_event_id), returns existing entry (no duplicate).
    """
    if not req.category_id and not (req.category_name or "").strip():
        raise HTTPException(status_code=400, detail="Provide category_id or category_name")
    with _db() as conn:
        # Idempotent: if we already have a log for this planned event, return it
        cur_ex = conn.execute(
            "SELECT id FROM time_entries WHERE user_id = ? AND planned_event_id = ? LIMIT 1",
            (SINGLE_USER_ID, req.event_id),
        )
        existing = cur_ex.fetchone()
        if existing:
            return JSONResponse({"ok": True, "entry_id": existing["id"]})

        cur = conn.execute(
            "SELECT title, start_at, end_at FROM planned_events_imported WHERE id = ? AND user_id = ?",
            (req.event_id, SINGLE_USER_ID),
        )
        ev = cur.fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="Event not found")

        category_id = req.category_id
        if not category_id and (req.category_name or "").strip():
            category_id = _get_or_create_category_by_name(conn, req.category_name.strip())
        if not category_id:
            raise HTTPException(status_code=400, detail="Could not resolve category")

        entry_id = f"te_{secrets.token_hex(10)}"
        title = req.title if req.title is not None else ev["title"]
        conn.execute(
            """
            INSERT INTO time_entries
              (id, user_id, start_at, end_at, title, category_id, tags_json, source, device, created_at, planned_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                SINGLE_USER_ID,
                ev["start_at"],
                ev["end_at"],
                title,
                category_id,
                "[]",
                "calendar_import",
                "web",
                _utc_now().isoformat(),
                req.event_id,
            ),
        )
        conn.commit()

    return JSONResponse({"ok": True, "entry_id": entry_id})


@app.post("/api/events/auto_categorize_all")
def api_auto_categorize_all() -> JSONResponse:
    """
    Auto-categorize all uncategorized events.
    """
    events = api_uncategorized_events(limit=100)
    categorized = 0

    with _db() as conn:
        cur = conn.execute(
            "SELECT id, name FROM categories WHERE user_id = ?",
            (SINGLE_USER_ID,),
        )
        cats = {r["name"].lower(): r["id"] for r in cur.fetchall()}

        for ev in events:
            # Skip if this planned event already has a time_entry (avoid duplicate logs)
            cur_ex = conn.execute(
                "SELECT 1 FROM time_entries WHERE user_id = ? AND planned_event_id = ? LIMIT 1",
                (SINGLE_USER_ID, ev.id),
            )
            if cur_ex.fetchone():
                continue

            smart_cat = _smart_categorize(ev.title, "")

            # Find matching category
            cat_id = ""
            for name, cid in cats.items():
                if smart_cat.lower() in name.lower() or name.lower() in smart_cat.lower():
                    cat_id = cid
                    break

            if not cat_id:
                cat_id = cats.get("other", list(cats.values())[0] if cats else "")

            if cat_id:
                entry_id = f"te_{secrets.token_hex(10)}"
                conn.execute(
                    """
                    INSERT INTO time_entries
                      (id, user_id, start_at, end_at, title, category_id, tags_json, source, device, created_at, planned_event_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        SINGLE_USER_ID,
                        ev.start_at.isoformat(),
                        ev.end_at.isoformat(),
                        ev.title,
                        cat_id,
                        "[]",
                        "auto_categorize",
                        "web",
                        _utc_now().isoformat(),
                        ev.id,
                    ),
                )
                categorized += 1

        conn.commit()

    return JSONResponse({"ok": True, "categorized": categorized})


def _generate_insights(category_minutes: dict[str, int], total_mins: int, days: int) -> list[dict[str, str]]:
    """
    Generate personalized insights based on time tracking data.
    """
    insights = []
    
    if total_mins < 60:
        return [{"type": "tip", "title": "Get started!", "text": "Log more activities to get personalized insights."}]
    
    # Calculate key metrics
    work_mins = category_minutes.get("Work", 0)
    wasted_mins = category_minutes.get("Unplanned / Wasted", 0)
    sleep_mins = category_minutes.get("Sleep", 0)
    learning_mins = category_minutes.get("Learning", 0)
    exercise_mins = category_minutes.get("Exercise", 0)
    
    work_hours = work_mins / 60
    wasted_hours = wasted_mins / 60
    sleep_hours_per_day = (sleep_mins / 60) / max(1, days)
    learning_hours = learning_mins / 60
    exercise_hours = exercise_mins / 60
    
    # Wasted time insights
    if wasted_hours > 2:
        insights.append({
            "type": "warning",
            "title": "Time leak detected",
            "text": f"You spent {round(wasted_hours, 1)}h on unplanned activities. Try blocking distracting sites during focus time."
        })
    elif wasted_hours > 0 and wasted_hours <= 2:
        insights.append({
            "type": "positive",
            "title": "Good control!",
            "text": f"Only {round(wasted_hours, 1)}h of unplanned time. Keep it up!"
        })
    
    # Sleep insights
    if sleep_hours_per_day < 6:
        insights.append({
            "type": "warning",
            "title": "Sleep deficit",
            "text": f"Averaging {round(sleep_hours_per_day, 1)}h sleep/day. Aim for 7-8h for better productivity."
        })
    elif sleep_hours_per_day >= 7 and sleep_hours_per_day <= 9:
        insights.append({
            "type": "positive",
            "title": "Healthy sleep!",
            "text": f"Great job maintaining {round(sleep_hours_per_day, 1)}h of sleep per day."
        })
    
    # Work insights
    if work_hours > 0:
        if days >= 5 and work_hours / days > 10:
            insights.append({
                "type": "warning",
                "title": "Overworking alert",
                "text": f"You're averaging {round(work_hours/days, 1)}h of work per day. Consider taking breaks."
            })
        elif work_hours >= 20 and days >= 5:
            insights.append({
                "type": "positive",
                "title": "Solid work week!",
                "text": f"You logged {round(work_hours, 1)}h of productive work time."
            })
    
    # Learning insights
    if learning_hours >= 3:
        insights.append({
            "type": "positive",
            "title": "Growth mindset!",
            "text": f"You invested {round(learning_hours, 1)}h in learning. Keep building new skills!"
        })
    elif learning_hours == 0 and days >= 3:
        insights.append({
            "type": "tip",
            "title": "Learning opportunity",
            "text": "Consider dedicating 30min daily to learning something new."
        })
    
    # Exercise insights
    if exercise_hours >= 3:
        insights.append({
            "type": "positive",
            "title": "Active lifestyle!",
            "text": f"You logged {round(exercise_hours, 1)}h of exercise. Great for energy and focus!"
        })
    elif exercise_hours == 0 and days >= 3:
        insights.append({
            "type": "tip",
            "title": "Move more",
            "text": "Even a 20-minute walk can boost creativity and reduce stress."
        })
    
    # Default if no specific insights
    if not insights:
        insights.append({
            "type": "tip",
            "title": "Keep tracking!",
            "text": "The more you log, the better insights you'll get about your time usage."
        })
    
    return insights


def _generate_ai_insights(
    detail_log: str, category_breakdown: str, days: int, lang: str = "en", model: str | None = None
) -> tuple[list[dict[str, str]], str | None]:
    """Use AI Builders. model: optional override (e.g. gpt-5, gemini-2.5-pro); else from env."""
    model = (model or "").strip() or os.getenv("AI_BUILDER_ANALYSIS_MODEL", "gpt-5")
    if not detail_log.strip():
        return [], None
    lang_instruction = "Respond only in 简体中文 (Simplified Chinese)." if (lang or "").lower() == "zh" else "Respond only in English."
    prompt = f"""You are a time-management coach. Based on this person's detailed time log over {days} days, give 3–5 short, actionable suggestions. Be specific (refer to their actual activities), not generic. {lang_instruction}

Time breakdown by category:
{category_breakdown}

Detailed log (recent entries with title and duration):
{detail_log}

Respond with exactly one suggestion per line, in this format:
• [Title]: [One specific actionable suggestion in 1–2 sentences]

Focus on: balance (work vs rest), sleep, focus blocks, wasted time, learning, relationships, and one concrete next step they can do tomorrow."""
    content, err = _ai_builder_chat(model, [{"role": "user", "content": prompt}], max_tokens=1000)
    if err or not content:
        return [], err
    insights = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("•") or line.startswith("-"):
            line = line[1:].strip()
        if ":" in line:
            title, _, text = line.partition(":")
            title, text = title.strip(), text.strip()
            if title and text:
                insights.append({"type": "tip", "title": title, "text": text})
        elif line:
            insights.append({"type": "tip", "title": "Suggestion", "text": line})
    return insights[:6], None


@app.get("/api/analytics/range")
def api_analytics_range(
    start_date: str, end_date: str, lang: str = "en", model: str | None = None
) -> dict[str, Any]:
    """
    Get analytics for a custom date range.
    lang: en | zh for AI insights language. model: optional AI model for insights (e.g. gpt-5).
    """
    tz = _get_user_tz(SINGLE_USER_ID)
    
    try:
        start_local = datetime.fromisoformat(start_date).replace(tzinfo=tz)
        end_local = datetime.fromisoformat(end_date).replace(tzinfo=tz) + timedelta(days=1)
    except:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    days_in_range = max(1, (end_local.date() - start_local.date()).days)
    
    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.start_at, te.end_at, te.title, c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()
    
    category_minutes: dict[str, int] = {}
    total_logged_mins = 0
    
    for r in rows:
        try:
            s = datetime.fromisoformat(r["start_at"])
            e = datetime.fromisoformat(r["end_at"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            s = max(s, start_utc)
            e = min(e, end_utc)
            mins = int((e - s).total_seconds() / 60)
            if mins <= 0:
                continue
            # Exclude whole-day events (>= 23 hours) from stats - they shouldn't count as 24h
            if mins >= 23 * 60:
                continue
            
            smart_cat = _smart_categorize(r["title"], r["category_name"])
            category_minutes[smart_cat] = category_minutes.get(smart_cat, 0) + mins
            total_logged_mins += mins
        except:
            continue
    
    total_range_mins = days_in_range * 24 * 60
    
    breakdown = []
    for cat, mins in sorted(category_minutes.items(), key=lambda x: -x[1]):
        breakdown.append({
            "category": cat,
            "minutes": mins,
            "hours": round(mins / 60, 1),
            "percent": round(mins / max(1, total_logged_mins) * 100, 1),
        })
    
    # Calculate productive vs wasted hours
    productive_categories = ["Work", "Learning", "Exercise", "Intimate / Quality Time"]
    wasted_categories = ["Unplanned / Wasted", "Unplanned wasting"]
    
    productive_mins = sum(category_minutes.get(cat, 0) for cat in productive_categories)
    wasted_mins = sum(category_minutes.get(cat, 0) for cat in wasted_categories)
    
    category_breakdown_str = "\n".join(
        f"- {b['category']}: {b['hours']}h ({b['percent']}%)" for b in breakdown
    )
    detail_lines = []
    tz = _get_user_tz(SINGLE_USER_ID)
    for r in rows[:80]:
        try:
            s = datetime.fromisoformat(r["start_at"]).astimezone(tz)
            e = datetime.fromisoformat(r["end_at"]).astimezone(tz)
            day = s.strftime("%a %d")
            seg = f"{day} {s.strftime('%H:%M')}-{e.strftime('%H:%M')} {r['category_name'] or '?'}: {r['title'] or '(no title)'}"
            detail_lines.append(seg)
        except Exception:
            continue
    detail_log = "\n".join(detail_lines)
    insights, insights_error = _generate_ai_insights(
        detail_log, category_breakdown_str, days_in_range, lang=lang, model=model
    )
    if not insights:
        insights = _generate_insights(category_minutes, total_logged_mins, days_in_range)
    
    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days_in_range,
        "total_logged_minutes": total_logged_mins,
        "total_logged_hours": round(total_logged_mins / 60, 1),
        "productive_hours": round(productive_mins / 60, 1),
        "wasted_hours": round(wasted_mins / 60, 1),
        "breakdown": breakdown,
        "insights_error": insights_error,
        "coverage_percent": round(total_logged_mins / total_range_mins * 100, 1),
        "insights": insights,
    }


@app.get("/api/analytics/daily_breakdown")
def api_analytics_daily_breakdown(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """
    Per-day breakdown for a date range. Returns list of
    { "date": "YYYY-MM-DD", "categories": { "Work": mins, ... }, "total_minutes": n }.
    Used for the past-N-days summary chart (category trends over time).
    """
    tz = _get_user_tz(SINGLE_USER_ID)
    try:
        start_local = datetime.fromisoformat(start_date).replace(tzinfo=tz)
        end_local = datetime.fromisoformat(end_date).replace(tzinfo=tz) + timedelta(days=1)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format")
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    with _db() as conn:
        cur = conn.execute(
            """
            SELECT te.start_at, te.end_at, te.title, c.name AS category_name
            FROM time_entries te
            JOIN categories c ON c.id = te.category_id
            WHERE te.user_id = ?
              AND te.start_at < ?
              AND te.end_at > ?
            ORDER BY te.start_at ASC
            """,
            (SINGLE_USER_ID, end_utc.isoformat(), start_utc.isoformat()),
        )
        rows = cur.fetchall()

    # day -> { category -> minutes }
    day_data: dict[str, dict[str, int]] = {}
    def ensure_day(d: str) -> dict[str, int]:
        if d not in day_data:
            day_data[d] = {}
        return day_data[d]

    for r in rows:
        try:
            s = datetime.fromisoformat(r["start_at"])
            e = datetime.fromisoformat(r["end_at"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            s_local = s.astimezone(tz)
            e_local = e.astimezone(tz)
            # Clip to range
            s_local = max(s_local, start_local)
            e_local = min(e_local, end_local)
            mins_total = int((e_local - s_local).total_seconds() / 60)
            if mins_total <= 0:
                continue
            if mins_total >= 23 * 60:
                continue
            smart_cat = _smart_categorize(r["title"], r["category_name"])
            # Split by calendar day in user TZ
            cur_start = s_local
            while cur_start < e_local:
                day_str = cur_start.strftime("%Y-%m-%d")
                day_end = cur_start.replace(hour=23, minute=59, second=59, microsecond=999999)
                segment_end = min(day_end, e_local)
                seg_mins = int((segment_end - cur_start).total_seconds() / 60)
                if seg_mins > 0:
                    cat_map = ensure_day(day_str)
                    cat_map[smart_cat] = cat_map.get(smart_cat, 0) + seg_mins
                cur_start = (cur_start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            continue

    # Build ordered list of days in range
    out = []
    d = start_local.date()
    end_d = end_local.date()
    while d < end_d:
        day_str = d.strftime("%Y-%m-%d")
        cat_map = day_data.get(day_str, {})
        total = sum(cat_map.values())
        out.append({
            "date": day_str,
            "categories": cat_map,
            "total_minutes": total,
        })
        d += timedelta(days=1)
    return out


class PlanningAdviceIn(BaseModel):
    goals: list[str] = []
    constraints: list[str] = []
    model: str | None = None  # e.g. gemini-2.5-pro, gpt-5; default gemini-2.5-pro


def _parse_planning_response(content: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Parse AI response into advice (list of {area, issue, suggestion, action}) and suggested_blocks."""
    import json
    advice: list[dict[str, str]] = []
    suggested_blocks: list[dict[str, str]] = []
    content = (content or "").strip()
    # Try JSON block first
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()
    elif "```" in content:
        start = content.find("```") + 3
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()
    try:
        data = json.loads(content)
        advice = [
            {
                "area": (x.get("area") or "").strip() or "Focus",
                "issue": (x.get("issue") or "").strip() or "",
                "suggestion": (x.get("suggestion") or "").strip() or "",
                "action": (x.get("action") or "").strip() or "",
            }
            for x in (data.get("advice") or data.get("recommendations") or [])
            if isinstance(x, dict)
        ]
        for b in data.get("suggested_blocks") or data.get("blocks") or []:
            if isinstance(b, dict) and (b.get("time") or b.get("activity")):
                suggested_blocks.append({
                    "time": str(b.get("time") or "").strip() or "—",
                    "activity": str(b.get("activity") or "").strip() or "—",
                })
    except Exception:
        pass
    return advice, suggested_blocks


@app.post("/api/ai/planning_advice")
def api_planning_advice(req: PlanningAdviceIn) -> dict[str, Any]:
    """
    AI-powered planning advice (AI Builders Space). Uses gemini-2.5-pro by default; optional model override.
    """
    # Use a model that returns direct text (gemini-2.5-pro); supermind-agent-v1 often returns empty
    model = (req.model or "").strip() or os.getenv("AI_BUILDER_PLANNING_MODEL", "gemini-2.5-pro")
    goals_text = "\n".join((req.goals or [])[:10]) or "(none given)"
    constraints_text = "\n".join((req.constraints or [])[:10]) or "(none given)"
    prompt = f"""You are a time-management coach. The user has shared their goals and constraints. Respond with valid JSON only (no markdown, no extra text).

Goals:
{goals_text}

Constraints:
{constraints_text}

Respond with exactly this structure (use only this JSON, no other text):
{{
  "advice": [
    {{ "area": "short area name", "issue": "what to watch", "suggestion": "one sentence", "action": "one concrete action" }}
  ],
  "suggested_blocks": [
    {{ "time": "e.g. 9:00 AM - 11:00 AM", "activity": "e.g. Deep work" }}
  ]
}}

Give 2-4 advice items and 3-5 suggested time blocks. Be specific to their goals and constraints."""

    content, err = _ai_builder_chat(model, [{"role": "user", "content": prompt}], max_tokens=1200)
    if content and not err:
        advice, suggested_blocks = _parse_planning_response(content)
        if advice or suggested_blocks:
            return {
                "advice": advice,
                "suggested_blocks": suggested_blocks,
                "insights_error": None,
            }
    # Fallback: rule-based, same shape as frontend expects
    advice = []
    if req.goals:
        for goal in (req.goals or [])[:3]:
            advice.append({
                "area": "Goals",
                "issue": f"Goal: {goal[:60]}",
                "suggestion": "Block 1–2 hours daily for this.",
                "action": "Add a recurring block in your calendar.",
            })
    if req.constraints:
        for c in (req.constraints or [])[:2]:
            advice.append({
                "area": "Constraints",
                "issue": c[:80],
                "suggestion": "Build buffer time around fixed commitments.",
                "action": "Mark buffers before/after in your week view.",
            })
    if not advice:
        advice = [
            {"area": "Focus", "issue": "No goals entered", "suggestion": "Protect 2 hours daily for deep work.", "action": "Block 9–11 AM as focus time."},
            {"area": "Review", "issue": "Weekly alignment", "suggestion": "Review your week every Sunday.", "action": "Set a 30-min Sunday review slot."},
        ]
    return {
        "advice": advice,
        "suggested_blocks": [
            {"time": "9:00 AM - 11:00 AM", "activity": "Deep work block"},
            {"time": "2:00 PM - 3:00 PM", "activity": "Admin / email"},
            {"time": "4:00 PM - 5:00 PM", "activity": "Learning / reading"},
        ],
        "insights_error": None,  # We have fallback content; don't show an error
    }


