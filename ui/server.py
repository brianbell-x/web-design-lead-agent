from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from src.core import (
    REPO_ROOT,
    ROOT_DIR,
    RUNS_DIR,
    STATE_DB_PATH,
    TERMINAL_DECISIONS,
    WorkflowStore,
    load_env_file,
    required_env,
)
from src.outreach import image_dimensions, parse_address, render_outreach_for_lead
from src.postgrid import send_letter
from src.zip_lookup import is_known_zip


load_env_file(ROOT_DIR / ".env")
load_env_file(REPO_ROOT / ".env")

UI_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(UI_DIR / "templates"))

POLL_INTERVAL_SEC = 0.75
HEARTBEAT_SEC = 15
TICKER_INITIAL_LIMIT = 8


def store() -> WorkflowStore:
    return WorkflowStore(STATE_DB_PATH)


def operator() -> dict:
    return {
        "agency_name": required_env("AGENCY_NAME"),
        "agency_email": required_env("AGENCY_EMAIL"),
        "signature_name": required_env("SIGNATURE_NAME"),
        "agency_phone": os.environ.get("AGENCY_PHONE", "").strip() or None,
        "agency_street": os.environ.get("AGENCY_STREET", "").strip() or None,
        "agency_city_state_zip": os.environ.get("AGENCY_CITY_STATE_ZIP", "").strip() or None,
    }


def parse_city_postcode(address: str | None) -> tuple[str, str, str]:
    """Return (city, state, postcode). State is upper-case 2-letter, empty if absent."""
    if not address:
        return "", "", ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if parts and parts[-1].upper() in ("USA", "UK", "US"):
        parts = parts[:-1]
    if len(parts) >= 3:
        city, zip_part = parts[1], parts[2]
    elif len(parts) == 2:
        city, zip_part = parts[0], parts[1]
    else:
        return parts[0] if parts else "", "", ""
    tokens = zip_part.split()
    state, postcode = "", ""
    if tokens and len(tokens[0]) == 2 and tokens[0].isalpha():
        state = tokens[0].upper()
        postcode = tokens[1] if len(tokens) > 1 else ""
    else:
        postcode = tokens[0] if tokens else ""
    return city, state, postcode


def format_ts(raw: str | None) -> str:
    """Format a stored ISO-ish timestamp as HH:MM:SS in local time."""
    if not raw:
        return ""
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return ""


def format_location(city: str, state: str, postcode: str) -> str:
    if not city:
        return ""
    tail = f"{state} {postcode}".strip()
    return f"{city}, {tail}" if tail else city


def lead_view(s: WorkflowStore, run_id: str, lead_id: str, op: dict) -> dict | None:
    lead = s.get_lead(lead_id)
    if not lead:
        return None
    screenshot = s._conn.execute(
        "SELECT artifact_id, path FROM artifacts WHERE run_id=? AND lead_id=? AND kind='screenshot'"
        " ORDER BY created_at DESC LIMIT 1",
        (run_id, lead_id),
    ).fetchone()
    mockup = s._conn.execute(
        "SELECT artifact_id, path FROM artifacts WHERE run_id=? AND lead_id=? AND kind='mockup'"
        " ORDER BY created_at DESC LIMIT 1",
        (run_id, lead_id),
    ).fetchone()
    if not screenshot or not mockup:
        return None
    screenshot_path = Path(screenshot["path"])
    mockup_path = Path(mockup["path"])
    if not screenshot_path.exists() or not mockup_path.exists():
        return None
    sw, sh = image_dimensions(screenshot_path)
    mw, mh = image_dimensions(mockup_path)
    city, state_code, postcode = parse_city_postcode(lead["address"])
    addr = parse_address(lead["address"] or "")
    today = datetime.now()
    pending_rows = s._conn.execute(QUALIFIED_SQL).fetchall()
    ids = [r["lead_id"] for r in pending_rows]
    try:
        idx = ids.index(lead_id)
        position = idx + 1
        prev_lead_id = ids[idx - 1] if idx > 0 else None
        next_lead_id = ids[idx + 1] if idx + 1 < len(ids) else None
    except ValueError:
        position = 0
        prev_lead_id = None
        next_lead_id = None
    total = len(ids)
    return {
        "run_id": run_id,
        "lead_id": lead_id,
        "name": lead["name"] or "",
        "category": display_category(lead["category"]),
        "location": format_location(city, state_code, postcode),
        "city": city,
        "screenshot_id": screenshot["artifact_id"],
        "mockup_id": mockup["artifact_id"],
        "screenshot_ar": round(sw / sh, 4),
        "mockup_ar": round(mw / mh, 4),
        "position": position,
        "total": total,
        "prev_lead_id": prev_lead_id,
        "next_lead_id": next_lead_id,
        "agency_name": op["agency_name"],
        "signature_name": op["signature_name"],
        "agency_email": op["agency_email"],
        "agency_phone": op["agency_phone"],
        "agency_street": op["agency_street"],
        "agency_city_state_zip": op["agency_city_state_zip"],
        "street": addr["street"],
        "city_state_zip": addr["city_state_zip"],
        "letter_date": today.strftime("%b %d · %Y").lower(),
        "letter_date_long": today.strftime("%B %d, %Y"),
    }


GENERIC_CATEGORIES = {"point_of_interest", "establishment", "place_of_worship", ""}


def display_category(raw: str | None) -> str:
    """Strip generic Google Places types ('point_of_interest', 'establishment') and humanize."""
    if not raw:
        return ""
    cat = raw.strip().lower()
    return "" if cat in GENERIC_CATEGORIES else cat.replace("_", " ")


QUALIFIED_SQL = f"""
SELECT sa.lead_id, sa.run_id, MIN(sa.completed_at) AS qualified_at
FROM step_attempts sa
JOIN step_attempts r
  ON r.lead_id = sa.lead_id AND r.run_id = sa.run_id
  AND r.step_name = 'review' AND r.status = 'completed'
  AND json_extract(r.payload_json, '$.needs_redesign') = 1
WHERE sa.step_name = 'mockup' AND sa.status = 'completed'
  AND sa.lead_id NOT IN (
    SELECT lead_id FROM lead_decisions
    WHERE decision IN ({", ".join("'" + d + "'" for d in sorted(TERMINAL_DECISIONS))})
  )
GROUP BY sa.lead_id, sa.run_id
ORDER BY qualified_at ASC
"""

SELECTION_SQL = QUALIFIED_SQL + " LIMIT 1"


def _workflow_state(conn) -> str:
    # 'abandoned' = previous run's subprocess died (set by recover_dead_runs).
    row = conn.execute(
        "SELECT status FROM runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "idle"
    if row["status"] == "running":
        return "running"
    if row["status"] == "abandoned":
        return "error"
    return "idle"


def next_pending(s: WorkflowStore) -> tuple[str, str] | None:
    row = s._conn.execute(SELECTION_SQL).fetchone()
    return (row["run_id"], row["lead_id"]) if row else None


def latest_running_run(conn) -> tuple[str, str, str | None] | None:
    row = conn.execute(
        "SELECT run_id, source_input, created_at FROM runs WHERE status='running'"
        " ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return (row["run_id"], row["source_input"], row["created_at"]) if row else None


def latest_run_id(conn) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def _run_started_ms(created_at: str | None) -> int | None:
    if not created_at:
        return None
    try:
        s = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def header_ctx(
    s: WorkflowStore, error: str | None, submitted_value: str | None
) -> dict:
    s.recover_dead_runs()
    ws = _workflow_state(s._conn)
    ctx = {
        "workflow_state": ws,
        "error": error,
        "submitted_value": submitted_value,
        "run_id": None,
        "source_label": None,
        "ticker_seed": [],
        "elapsed": None,
        "run_started_ms": None,
    }
    if ws == "running":
        running = latest_running_run(s._conn)
        if running:
            run_id, source_label, created_at = running
            ctx["run_id"] = run_id
            ctx["source_label"] = source_label
            ctx["ticker_seed"] = _initial_events(s._conn, run_id, TICKER_INITIAL_LIMIT)
            ctx["elapsed"] = "00:00:00"
            ctx["run_started_ms"] = _run_started_ms(created_at)
            return ctx
    last_id = latest_run_id(s._conn)
    if last_id:
        ctx["ticker_seed"] = _initial_events(s._conn, last_id, TICKER_INITIAL_LIMIT)
    return ctx


ZIP_RE = re.compile(r"^\d{5}$")
CITY_STATE_RE = re.compile(r"^([A-Za-z .'-]+),\s*([A-Za-z]{2})$")


def run_id_for_lead(s: WorkflowStore, lead_id: str) -> str | None:
    row = s._conn.execute(
        "SELECT run_id FROM run_leads WHERE lead_id=? ORDER BY ordinal DESC LIMIT 1",
        (lead_id,),
    ).fetchone()
    return row["run_id"] if row else None


def map_event(row: dict, lead_name_by_id: dict[str, str]) -> str | None:
    """Map a step_attempts or artifacts row to a ticker line, or None to suppress."""
    kind = row.get("event_kind")
    name = (lead_name_by_id.get(row.get("lead_id") or "") or "").strip()
    if kind == "artifact":
        ak = row.get("artifact_kind")
        if ak == "screenshot":
            return f"captured screenshot of {name}" if name else "captured screenshot"
        if ak == "mockup":
            return f"mockup ready for {name}" if name else "mockup ready"
        if ak == "outreach_pdf":
            return f"letter generated for {name}" if name else "letter generated"
        if ak == "source_receipt":
            md = row.get("metadata_json")
            zip_code = (json.loads(md).get("zip") or "").strip() if md else ""
            return f"lead list ready · {zip_code}" if zip_code else "lead list ready"
        return None
    if kind == "step":
        if row.get("status") != "completed":
            return None
        sn = row.get("step_name")
        payload = json.loads(row["payload_json"]) if row.get("payload_json") else {}
        who = name or "lead"
        if sn == "filter":
            if payload.get("exclude") is True:
                reason = (payload.get("exclude_reason") or "").strip()
                return f"excluded {who} ({reason})" if reason else f"excluded {who}"
            return f"kept {who}"
        if sn == "review":
            return (
                f"flagged {who} for redesign"
                if payload.get("needs_redesign")
                else f"no redesign needed for {who}"
            )
    return None


# ============= SSE POLLING =============


def _conn_ro() -> sqlite3.Connection:
    c = sqlite3.connect(STATE_DB_PATH, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _lead_names(c: sqlite3.Connection) -> dict[str, str]:
    return {
        r["lead_id"]: r["name"] for r in c.execute("SELECT lead_id, name FROM leads")
    }


def _initial_events(c: sqlite3.Connection, run_id: str, limit: int) -> list[dict]:
    """Return the most recent N ticker entries (newest first) as {ts, line} dicts."""
    rows = c.execute(
        """
        SELECT ts, event_kind, lead_id, artifact_kind, metadata_json,
               step_name, status, payload_json
        FROM (
            SELECT created_at AS ts, 'artifact' AS event_kind, lead_id,
                   kind AS artifact_kind, metadata_json,
                   NULL AS step_name, NULL AS status, NULL AS payload_json
            FROM artifacts WHERE run_id = ?
            UNION ALL
            SELECT completed_at AS ts, 'step' AS event_kind, lead_id,
                   NULL AS artifact_kind, NULL AS metadata_json,
                   step_name, status, payload_json
            FROM step_attempts WHERE run_id = ? AND completed_at IS NOT NULL
        )
        ORDER BY ts DESC LIMIT ?
        """,
        (run_id, run_id, limit),
    ).fetchall()
    names = _lead_names(c)
    out: list[dict] = []
    for r in rows:
        line = map_event(dict(r), names)
        if line:
            out.append({"ts": format_ts(r["ts"]), "line": line})
    return out


def _poll(run_id: str, last_ts: str) -> tuple[list[dict], str]:
    c = _conn_ro()
    try:
        rows = c.execute(
            """
            SELECT ts, event_kind, lead_id, artifact_kind, metadata_json,
                   step_name, status, payload_json
            FROM (
                SELECT created_at AS ts, 'artifact' AS event_kind, lead_id,
                       kind AS artifact_kind, metadata_json,
                       NULL AS step_name, NULL AS status, NULL AS payload_json
                FROM artifacts WHERE run_id = ? AND created_at > ?
                UNION ALL
                SELECT completed_at AS ts, 'step' AS event_kind, lead_id,
                       NULL AS artifact_kind, NULL AS metadata_json,
                       step_name, status, payload_json
                FROM step_attempts WHERE run_id = ? AND completed_at > ?
            )
            ORDER BY ts ASC
            """,
            (run_id, last_ts, run_id, last_ts),
        ).fetchall()
        names = _lead_names(c)
        out: list[dict] = []
        new_last = last_ts
        for r in rows:
            line = map_event(dict(r), names)
            if line:
                out.append({"ts": format_ts(r["ts"]), "line": line})
            if r["ts"] and r["ts"] > new_last:
                new_last = r["ts"]
        return out, new_last
    finally:
        c.close()


async def event_generator(run_id: str):
    last_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    while True:
        events, last_ts = await asyncio.to_thread(_poll, run_id, last_ts)
        for ev in events:
            yield {"event": "ticker", "data": json.dumps(ev)}
        await asyncio.sleep(POLL_INTERVAL_SEC)


# ============= APP =============

app = FastAPI()


ALLOWED_POST_ORIGINS = frozenset({"http://127.0.0.1:8765", "http://localhost:8765"})


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """Block cross-origin state-changing requests. The UI binds to loopback, so any
    POST must come from a same-origin page in the operator's own browser. Browsers
    send Origin for fetch and form POSTs from modern Chrome/Firefox/Safari; missing
    or mismatched Origin = treat as CSRF attempt."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin not in ALLOWED_POST_ORIGINS:
            return PlainTextResponse(
                f"Origin {origin!r} not permitted on loopback UI; expected one of {sorted(ALLOWED_POST_ORIGINS)}.",
                status_code=403,
            )
    return await call_next(request)

# Serialize POST /runs end-to-end so a second submission can't race past the
# `_workflow_state` guard during the ~3-6s window before the subprocess inserts
# its `runs` row. Uvicorn runs single-worker (see `main()`), so a single
# asyncio.Lock is sufficient.
_runs_lock = asyncio.Lock()


@app.get("/")
def index(
    request: Request,
    lead_id: str | None = None,
    error: str | None = None,
    value: str | None = None,
):
    s = store()
    try:
        op = operator()
        head = header_ctx(s, error=error, submitted_value=value)

        def empty(reason: str):
            return TEMPLATES.TemplateResponse(
                request, "empty.html", {**head, "reason": reason}
            )

        def approval(view: dict):
            return TEMPLATES.TemplateResponse(
                request,
                "approval.html",
                {**head, "lead": view, "ticker_seed": _seed_events(view["run_id"])},
            )

        if lead_id:
            run_id = run_id_for_lead(s, lead_id)
            if not run_id:
                return empty(f"lead {lead_id} not found")
            view = lead_view(s, run_id, lead_id, op)
            return approval(view) if view else empty(f"lead {lead_id} has no artifacts")

        nxt = next_pending(s)
        if not nxt:
            return empty("no leads awaiting your review")
        view = lead_view(s, nxt[0], nxt[1], op)
        return approval(view) if view else empty("no leads awaiting your review")
    finally:
        s.close()


@app.post("/runs")
async def start_run(location: str = Form(...)):
    raw = location.strip()
    if ZIP_RE.match(raw):
        # Reject 5-digit codes that aren't real US ZIPs before spawning a
        # subprocess that would die immediately on geocode failure.
        if not is_known_zip(raw):
            qs = urlencode({"error": "invalid_input", "value": raw})
            return RedirectResponse(url=f"/?{qs}", status_code=303)
        args = ["--zip", raw]
    else:
        m = CITY_STATE_RE.match(raw)
        if not m:
            qs = urlencode({"error": "invalid_input", "value": raw})
            return RedirectResponse(url=f"/?{qs}", status_code=303)
        args = ["--city", m.group(1).strip(), "--state", m.group(2).upper()]

    async with _runs_lock:
        s = store()
        try:
            # 'error' means the previous run was abandoned by recover_dead_runs;
            # the banner invites the operator to "start a new one," so allow it.
            if _workflow_state(s._conn) == "running":
                return RedirectResponse(url="/?error=run_in_progress", status_code=303)
        finally:
            s.close()

        log_path = STATE_DB_PATH.parent / "runner.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log_handle:
            log_handle.write(
                f"\n--- {datetime.now(timezone.utc).isoformat()} {' '.join(args)} ---\n".encode()
            )
            log_handle.flush()
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src.runner",
                *args,
                cwd=str(ROOT_DIR),
                stdout=log_handle,
                stderr=asyncio.subprocess.STDOUT,
            )

        # Hold the lock until the subprocess registers its `runs` row. Python
        # boot + Playwright import takes ~3-6s; releasing earlier would let a
        # second POST pass the guard and spawn a duplicate runner.
        deadline = asyncio.get_event_loop().time() + 15.0
        while asyncio.get_event_loop().time() < deadline:
            s = store()
            try:
                if _workflow_state(s._conn) == "running":
                    break
            finally:
                s.close()
            await asyncio.sleep(0.2)

    return RedirectResponse(url="/", status_code=303)


def _seed_events(run_id: str) -> list[str]:
    c = _conn_ro()
    try:
        return _initial_events(c, run_id, TICKER_INITIAL_LIMIT)
    finally:
        c.close()


@app.get("/events/{run_id}")
async def events(run_id: str):
    return EventSourceResponse(event_generator(run_id), ping=HEARTBEAT_SEC)


@app.get("/artifact/{artifact_id}")
def artifact(artifact_id: str):
    s = store()
    try:
        row = s._conn.execute(
            "SELECT path FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404)
        p = Path(row["path"]).resolve()
        runs_root = RUNS_DIR.resolve()
        if runs_root not in p.parents:
            raise HTTPException(404)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=300"})
    finally:
        s.close()


def _output_dir_for_lead(s: WorkflowStore, run_id: str, lead_id: str) -> Path:
    """Use the parent directory of the existing screenshot/mockup artifact so
    we render into the actual on-disk lead folder, not a recomputed slug."""
    row = s._conn.execute(
        "SELECT path FROM artifacts WHERE run_id=? AND lead_id=? AND kind IN ('mockup','screenshot')"
        " ORDER BY created_at DESC LIMIT 1",
        (run_id, lead_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, detail="No artifact directory for lead")
    return Path(row["path"]).parent


def _record_decision_or_404(decision: str, lead_id: str) -> str:
    s = store()
    try:
        run_id = run_id_for_lead(s, lead_id)
        if not run_id:
            raise HTTPException(404)
        s.record_decision(lead_id=lead_id, decision=decision, run_id=run_id)
        return run_id
    finally:
        s.close()


@app.post("/leads/{lead_id}/approve")
async def approve(lead_id: str):
    s = store()
    try:
        run_id = run_id_for_lead(s, lead_id)
        if not run_id:
            raise HTTPException(404)
        s.record_decision(lead_id=lead_id, decision="approve", run_id=run_id)
        output_dir = _output_dir_for_lead(s, run_id, lead_id)
        # Playwright PDF render is sync (~3-6s); blocks the event loop. Acceptable
        # for single-operator loopback UI; revisit if SSE ticker lag becomes an issue.
        render_outreach_for_lead(lead_id, run_id, s, output_dir, **operator())
        lead = dict(s.get_lead(lead_id))
        try:
            await send_letter(
                lead_id=lead_id, run_id=run_id, lead=lead,
                pdf_path=output_dir / "outreach.pdf",
                sender=operator(), store=s,
            )
        except Exception as error:
            qs = urlencode({"lead_id": lead_id, "error": f"postgrid: {error}"})
            return RedirectResponse(url=f"/?{qs}", status_code=303)
        return RedirectResponse(url="/", status_code=303)
    finally:
        s.close()


@app.post("/leads/{lead_id}/skip")
def skip(lead_id: str):
    _record_decision_or_404("skip", lead_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/leads/{lead_id}/reject")
def reject(lead_id: str):
    _record_decision_or_404("reject", lead_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/leads/{lead_id}/revise")
def revise(lead_id: str):
    _record_decision_or_404("revise", lead_id)
    return RedirectResponse(url=f"/?lead_id={lead_id}", status_code=303)


@app.post("/leads/{lead_id}/regenerate")
def regenerate(lead_id: str):
    _record_decision_or_404("regenerate", lead_id)
    return RedirectResponse(url=f"/?lead_id={lead_id}", status_code=303)


def _adjacent_pending(s: WorkflowStore, lead_id: str, direction: int) -> str | None:
    """Resolve the lead_id adjacent to `lead_id` in the qualified-pending queue.
    direction: -1 = previous, +1 = next."""
    ids = [r["lead_id"] for r in s._conn.execute(QUALIFIED_SQL).fetchall()]
    if lead_id not in ids:
        return None
    i = ids.index(lead_id) + direction
    return ids[i] if 0 <= i < len(ids) else None


@app.get("/leads/{lead_id}/prev")
def prev_lead(lead_id: str):
    s = store()
    try:
        target = _adjacent_pending(s, lead_id, -1)
        return RedirectResponse(
            url=f"/?lead_id={target}" if target else "/", status_code=303
        )
    finally:
        s.close()


@app.get("/leads/{lead_id}/next")
def next_lead(lead_id: str):
    s = store()
    try:
        target = _adjacent_pending(s, lead_id, +1)
        return RedirectResponse(
            url=f"/?lead_id={target}" if target else "/", status_code=303
        )
    finally:
        s.close()


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
