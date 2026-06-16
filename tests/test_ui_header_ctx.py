from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.core import WorkflowStore
from ui.server import header_ctx


def _store_with_run(status: str, with_active_step: bool = False) -> WorkflowStore:
    s = WorkflowStore(":memory:")
    s.create_run(run_id="r1", source_input="78704")
    if status != "running":
        s._conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, "r1"))
    if with_active_step:
        s._conn.execute(
            "INSERT INTO step_attempts (step_attempt_id, run_id, lead_id, step_name, "
            "input_hash, status, started_at) VALUES (?,?,?,?,?,?,?)",
            ("step_x", "r1", None, "discover", "h", "running", "2026-05-08T00:00:00Z"),
        )
    return s


def test_header_ctx_idle_when_no_runs():
    s = WorkflowStore(":memory:")
    ctx = header_ctx(s, error=None, submitted_value=None)
    assert ctx["workflow_state"] == "idle"
    assert ctx["run_id"] is None
    assert ctx["source_label"] is None
    assert ctx["ticker_seed"] == []
    assert ctx["error"] is None
    assert ctx["submitted_value"] is None


def test_header_ctx_running_populates_run_id_and_label():
    s = _store_with_run(status="running", with_active_step=True)
    ctx = header_ctx(s, error=None, submitted_value=None)
    assert ctx["workflow_state"] == "running"
    assert ctx["run_id"] == "r1"
    assert ctx["source_label"] == "78704"


def test_header_ctx_running_during_startup_window():
    # A fresh run row exists with no step_attempts yet (subprocess still
    # importing Playwright). The startup gap must NOT register as 'error' —
    # it stays 'running' so the operator sees scanning state, not the
    # "previous run died" banner.
    s = _store_with_run(status="running", with_active_step=False)
    ctx = header_ctx(s, error=None, submitted_value=None)
    assert ctx["workflow_state"] == "running"
    assert ctx["run_id"] == "r1"
    assert ctx["source_label"] == "78704"


def test_header_ctx_idle_when_last_run_completed():
    s = _store_with_run(status="completed")
    ctx = header_ctx(s, error=None, submitted_value=None)
    assert ctx["workflow_state"] == "idle"


def test_header_ctx_passes_through_error_and_value():
    s = WorkflowStore(":memory:")
    ctx = header_ctx(s, error="invalid_input", submitted_value="asdf")
    assert ctx["error"] == "invalid_input"
    assert ctx["submitted_value"] == "asdf"


def test_sweep_demotes_run_with_dead_pid():
    s = WorkflowStore(":memory:")
    s._conn.execute(
        "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
        " VALUES (?,?,?,?,?)",
        ("old1", "z", "2020-01-01T00:00:00Z", "running", 999999999),
    )
    ctx = header_ctx(s, error=None, submitted_value=None)
    row = s._conn.execute("SELECT status FROM runs WHERE run_id='old1'").fetchone()
    assert row["status"] == "abandoned"
    # 'abandoned' is the recovery signal for a crashed run — workflow_state
    # surfaces it as 'error' so the _runform.html banner ("previous run died
    # unexpectedly") can render.
    assert ctx["workflow_state"] == "error"


def test_sweep_leaves_run_with_live_pid():
    # Use a real spawned subprocess, not os.getpid(). On Windows the broken
    # os.kill(pid, 0) probe happens to succeed for the current process but
    # raises winerror 87 for any other live PID, so a self-pid test passed
    # while production was wrongly marking every live run as abandoned.
    import subprocess, sys, os
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.pid != os.getpid()
        s = WorkflowStore(":memory:")
        s._conn.execute(
            "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
            " VALUES (?,?,?,?,?)",
            ("old2", "z", "2020-01-01T00:00:00Z", "running", proc.pid),
        )
        header_ctx(s, error=None, submitted_value=None)
        row = s._conn.execute("SELECT status FROM runs WHERE run_id='old2'").fetchone()
        assert row["status"] == "running"
    finally:
        proc.kill()
        proc.wait()
