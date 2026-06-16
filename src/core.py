"""Core: paths/env, helpers, SQLite store, CLI parsing.

The DB (built by WorkflowStore) is the source of truth for
run/lead/step/artifact/provider state. JSON files on disk are exports or
raw vendor receipts — never primary state.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.zip_lookup import get_zip_codes


# ---------- Paths and environment ----------

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT_DIR.parent
RUNS_DIR = ROOT_DIR / "data" / "runs"
STATE_DB_PATH = ROOT_DIR / "data" / "state" / "agent.db"


def load_env_file(file_path: Path) -> None:
    if not file_path.exists():
        return

    for line in file_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if not match or os.environ.get(match.group(1)):
            continue

        value = match.group(2).strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[match.group(1)] = value


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to web-design-lead-agent/.env or the shell environment."
        )
    return value


def rel(file_path: Path | str) -> str:
    return os.path.relpath(Path(file_path), ROOT_DIR).replace("\\", "/")


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


# ---------- Helpers: time, slugs, JSON, HTTP, hashing ----------


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_name(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower())
    slug = slug.strip("_")[:120]
    return slug or "unknown"


def file_slug(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower())
    slug = slug.strip("-")[:80]
    return slug or "unknown"


def write_json(file_path: Path, value: object) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(f"{json.dumps(value, indent=2)}\n", encoding="utf-8")


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: object | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict]:
    try:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as error:
        raise LeadSkip(
            f"http {method} {url}: {error.__class__.__name__}: {error}"
        ) from error
    raw = response.content
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        body = {"error": {"message": raw.decode("utf-8", errors="replace")}}
    return response.status_code, body


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- SQLite-backed workflow store ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    source_input TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    runner_pid INTEGER
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    place_id TEXT UNIQUE,
    name TEXT,
    category TEXT,
    address TEXT,
    phone TEXT,
    website TEXT
);

CREATE TABLE IF NOT EXISTS run_leads (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    lead_id TEXT NOT NULL REFERENCES leads(lead_id),
    ordinal INTEGER,
    PRIMARY KEY (run_id, lead_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(run_id),
    lead_id TEXT REFERENCES leads(lead_id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS artifacts_lookup ON artifacts(run_id, lead_id, kind);

CREATE TABLE IF NOT EXISTS step_attempts (
    step_attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    lead_id TEXT REFERENCES leads(lead_id),
    step_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT,
    payload_json TEXT,
    cost_usd REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS step_attempts_idem
    ON step_attempts(COALESCE(lead_id, ''), step_name, input_hash);
CREATE INDEX IF NOT EXISTS step_attempts_by_run
    ON step_attempts(run_id, step_name);

CREATE TABLE IF NOT EXISTS lead_decisions (
    lead_id TEXT NOT NULL REFERENCES leads(lead_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    decision TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, decided_at)
);
CREATE INDEX IF NOT EXISTS lead_decisions_by_lead
    ON lead_decisions(lead_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS postgrid_sends (
    postgrid_send_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(lead_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    mode TEXT NOT NULL,
    provider_letter_id TEXT,
    status TEXT NOT NULL,
    cost_usd REAL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS postgrid_sends_idem
    ON postgrid_sends(lead_id, run_id);
"""


class LeadSkip(Exception):
    """This lead can't proceed; the run is fine."""


TERMINAL_DECISIONS = frozenset({"approve", "skip", "reject"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _iso_age_seconds(then_iso: str, now_iso: str) -> float:
    then = datetime.fromisoformat(then_iso.replace("Z", "+00:00"))
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return (now - then).total_seconds()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259  # GetExitCodeProcess sentinel when the process is running

    def _pid_alive(pid: int) -> bool:
        # os.kill(pid, 0) is unreliable on Windows: it raises ERROR_INVALID_PARAMETER
        # (winerror 87) for both alive and dead foreign PIDs, so it can only correctly
        # detect the current process. Use OpenProcess + GetExitCodeProcess instead.
        if pid <= 0:
            return False
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
        )
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
else:
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we lack permission to signal it
        return True


class WorkflowStore:
    def __init__(self, db_path):
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def create_run(self, *, run_id: str, source_input: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
            " VALUES (?, ?, ?, 'running', NULL)",
            (run_id, source_input, _now_iso()),
        )

    def set_runner_pid(self, *, run_id: str, pid: int) -> None:
        self._conn.execute(
            "UPDATE runs SET runner_pid=? WHERE run_id=?", (pid, run_id)
        )

    def recover_dead_runs(self, *, null_pid_grace_seconds: int = 60) -> int:
        """Demote running runs whose PID is dead. NULL pid within the grace
        window means the runner hasn't reported in yet — leave alone."""
        rows = self._conn.execute(
            "SELECT run_id, runner_pid, created_at FROM runs WHERE status='running'"
        ).fetchall()
        abandoned = 0
        now_iso = _now_iso()
        for row in rows:
            pid = row["runner_pid"]
            if pid is None:
                age_s = _iso_age_seconds(row["created_at"], now_iso)
                if age_s < null_pid_grace_seconds:
                    continue
            elif _pid_alive(pid):
                continue
            self._conn.execute(
                "UPDATE runs SET status='abandoned' WHERE run_id=?", (row["run_id"],)
            )
            self._conn.execute(
                "UPDATE step_attempts SET status='failed', completed_at=?,"
                " error_message='abandoned' WHERE run_id=? AND status='running'",
                (now_iso, row["run_id"]),
            )
            abandoned += 1
        return abandoned

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def complete_run(self, run_id: str, *, status: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id)
        )
        # Demote stranded 'running' step_attempts so they don't leak into the
        # ticker or the qualified-pending queue after the run is terminal.
        self._conn.execute(
            "UPDATE step_attempts SET status='failed', completed_at=?,"
            " error_message='run_terminated' WHERE run_id=? AND status='running'",
            (_now_iso(), run_id),
        )

    def upsert_lead(
        self, *, place_id: str | None, name, category, address, phone, website
    ) -> str:
        if place_id:
            existing = self._conn.execute(
                "SELECT lead_id FROM leads WHERE place_id = ?", (place_id,)
            ).fetchone()
            if existing:
                lead_id = existing["lead_id"]
                self._conn.execute(
                    "UPDATE leads SET name=?, category=?, address=?, phone=?,"
                    " website=? WHERE lead_id=?",
                    (name, category, address, phone, website, lead_id),
                )
                return lead_id
        lead_id = _new_id("lead")
        self._conn.execute(
            "INSERT INTO leads (lead_id, place_id, name, category, address, phone, website)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lead_id, place_id, name, category, address, phone, website),
        )
        return lead_id

    def get_lead(self, lead_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM leads WHERE lead_id = ?", (lead_id,)
        ).fetchone()

    def link_lead_to_run(self, *, run_id: str, lead_id: str, ordinal: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO run_leads (run_id, lead_id, ordinal) VALUES (?, ?, ?)",
            (run_id, lead_id, ordinal),
        )

    def list_run_leads(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT l.*, rl.ordinal FROM run_leads rl"
                " JOIN leads l ON l.lead_id = rl.lead_id"
                " WHERE rl.run_id = ? ORDER BY rl.ordinal",
                (run_id,),
            )
        )

    def record_artifact(
        self,
        *,
        run_id: str | None,
        lead_id: str | None,
        kind: str,
        path,
        metadata: dict | None = None,
    ) -> str:
        artifact_id = _new_id("art")
        self._conn.execute(
            "INSERT INTO artifacts (artifact_id, run_id, lead_id, kind, path, created_at, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                run_id,
                lead_id,
                kind,
                str(Path(path)),
                _now_iso(),
                json.dumps(metadata) if metadata else None,
            ),
        )
        return artifact_id

    def start_step(
        self, *, run_id: str, lead_id: str | None, step_name: str, input_hash: str
    ) -> str:
        existing = self._conn.execute(
            "SELECT step_attempt_id FROM step_attempts"
            " WHERE COALESCE(lead_id,'')=COALESCE(?,'')"
            " AND step_name=? AND input_hash=?",
            (lead_id, step_name, input_hash),
        ).fetchone()
        if existing:
            return existing["step_attempt_id"]
        attempt_id = _new_id("step")
        self._conn.execute(
            "INSERT INTO step_attempts (step_attempt_id, run_id, lead_id, step_name,"
            " input_hash, status, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?)",
            (attempt_id, run_id, lead_id, step_name, input_hash, _now_iso()),
        )
        return attempt_id

    def complete_step(
        self, step_attempt_id: str, *, payload: dict | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE step_attempts SET status='completed', completed_at=?, payload_json=?,"
            " cost_usd=? WHERE step_attempt_id=?",
            (
                _now_iso(),
                json.dumps(payload) if payload is not None else None,
                cost_usd,
                step_attempt_id,
            ),
        )

    def fail_step(self, step_attempt_id: str, *, error_message: str) -> None:
        self._conn.execute(
            "UPDATE step_attempts SET status='failed', completed_at=?, error_message=?"
            " WHERE step_attempt_id=?",
            (_now_iso(), error_message, step_attempt_id),
        )

    def record_decision(self, *, lead_id: str, decision: str, run_id: str) -> None:
        """Append-only audit row. decision ∈ {approve, skip, reject, revise, regenerate}."""
        self._conn.execute(
            "INSERT INTO lead_decisions (lead_id, run_id, decision, decided_at)"
            " VALUES (?, ?, ?, ?)",
            (lead_id, run_id, decision, _now_iso()),
        )

    def get_decision(self, lead_id: str) -> sqlite3.Row | None:
        """Latest decision for the lead, or None if undecided."""
        return self._conn.execute(
            "SELECT * FROM lead_decisions WHERE lead_id = ?"
            " ORDER BY decided_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()

    def total_cost_usd(self, run_id: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM ("
            " SELECT cost_usd FROM step_attempts WHERE run_id = ?"
            " UNION ALL"
            " SELECT cost_usd FROM postgrid_sends WHERE run_id = ?"
            ")",
            (run_id, run_id),
        ).fetchone()
        return round(row["total"] or 0.0, 6)

    def record_postgrid_send(self, *, lead_id: str, run_id: str, mode: str) -> str:
        existing = self._conn.execute(
            "SELECT postgrid_send_id FROM postgrid_sends WHERE lead_id=? AND run_id=?",
            (lead_id, run_id),
        ).fetchone()
        if existing:
            return existing["postgrid_send_id"]
        send_id = _new_id("pg")
        self._conn.execute(
            "INSERT INTO postgrid_sends (postgrid_send_id, lead_id, run_id, mode,"
            " status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (send_id, lead_id, run_id, mode, _now_iso()),
        )
        return send_id

    def update_postgrid_send(
        self, *, postgrid_send_id: str, status: str,
        provider_letter_id: str | None = None, cost_usd: float | None = None,
        error_message: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE postgrid_sends SET status=?, provider_letter_id=COALESCE(?, provider_letter_id),"
            " cost_usd=COALESCE(?, cost_usd), error_message=COALESCE(?, error_message),"
            " updated_at=? WHERE postgrid_send_id=?",
            (status, provider_letter_id, cost_usd, error_message, _now_iso(), postgrid_send_id),
        )

    def get_postgrid_send(self, lead_id: str, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM postgrid_sends WHERE lead_id=? AND run_id=?",
            (lead_id, run_id),
        ).fetchone()


# ---------- CLI argument parsing ----------


def arg_value(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def positional_zip() -> str | None:
    skip_next = False
    for value in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if value in {"--zip", "--city", "--state"}:
            skip_next = True
            continue
        if value.startswith("--"):
            continue
        return value
    return None


def requested_zip_codes() -> tuple[str, list[str]]:
    city = arg_value("--city")
    state = arg_value("--state")
    zip_code = arg_value("--zip") or positional_zip()

    if zip_code and (city or state):
        raise RuntimeError("Use either a ZIP code or --city with --state, not both.")

    if city or state:
        if not city or not state:
            raise RuntimeError(
                "--city and --state are both required for city/state lookup."
            )
        zip_codes = get_zip_codes(city, state)
        if not zip_codes:
            raise RuntimeError(
                f"No GeoNames ZIP codes found for {city.strip()}, {state.strip()}."
            )
        return f"{city.strip()}, {state.strip()}", zip_codes

    zip_code = zip_code or input("ZIP code: ").strip()
    if not zip_code:
        raise RuntimeError("ZIP code is required.")
    return zip_code, [zip_code]
