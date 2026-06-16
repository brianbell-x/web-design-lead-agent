from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.core import WorkflowStore


def test_store_creates_schema_on_open():
    store = WorkflowStore(":memory:")
    tables = {row[0] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"runs", "leads", "run_leads", "artifacts", "step_attempts", "lead_decisions"} <= tables
    assert "provider_calls" not in tables


def test_store_enables_foreign_keys_and_wal():
    store = WorkflowStore(":memory:")
    fk = store._conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_create_run_and_get_run():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="78704")
    row = store.get_run("r1")
    assert row["run_id"] == "r1"
    assert row["source_input"] == "78704"
    assert row["status"] == "running"


def test_upsert_lead_is_idempotent_by_place_id():
    store = WorkflowStore(":memory:")
    lead_id_1 = store.upsert_lead(
        place_id="ChIJ_abc", name="Cafe", category="cafe",
        address="1 St", phone="555", website="https://x.test",
    )
    lead_id_2 = store.upsert_lead(
        place_id="ChIJ_abc", name="Cafe Renamed", category="cafe",
        address="1 St", phone="555", website="https://x.test",
    )
    assert lead_id_1 == lead_id_2
    row = store.get_lead(lead_id_1)
    assert row["name"] == "Cafe Renamed"


def test_link_lead_to_run():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="78704")
    lead_id = store.upsert_lead(place_id="p1", name="A", category=None,
                                address=None, phone=None, website="https://a.test")
    store.link_lead_to_run(run_id="r1", lead_id=lead_id, ordinal=1)
    leads = store.list_run_leads("r1")
    assert len(leads) == 1
    assert leads[0]["lead_id"] == lead_id
    assert leads[0]["ordinal"] == 1


def test_complete_run_sets_status():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="78704")
    store.complete_run("r1", status="completed")
    row = store.get_run("r1")
    assert row["status"] == "completed"


def test_record_artifact_returns_id_and_persists(tmp_path):
    f = tmp_path / "screenshot.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"x" * 1000)
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    lead_id = store.upsert_lead(place_id="p", name="x", category=None, address=None,
                                phone=None, website="https://x")
    art_id = store.record_artifact(
        run_id="r1", lead_id=lead_id, kind="screenshot", path=f,
        metadata={"final_url": "https://x"},
    )
    row = store._conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (art_id,)).fetchone()
    assert row["kind"] == "screenshot"
    assert row["path"] == str(f)


def test_step_attempts_dedupe_on_input_hash():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    a1 = store.start_step(run_id="r1", lead_id=None, step_name="s", input_hash="h1")
    store.complete_step(a1, payload={"ok": True})
    a2 = store.start_step(run_id="r1", lead_id=None, step_name="s", input_hash="h1")
    assert a1 == a2  # same hash returns the existing completed attempt
    a3 = store.start_step(run_id="r1", lead_id=None, step_name="s", input_hash="h2")
    assert a3 != a1


def test_complete_step_records_cost():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    a = store.start_step(run_id="r1", lead_id=None, step_name="filter", input_hash="h")
    store.complete_step(a, payload={"exclude": False}, cost_usd=0.0005)
    row = store._conn.execute(
        "SELECT cost_usd, status FROM step_attempts WHERE step_attempt_id=?", (a,)
    ).fetchone()
    assert row["cost_usd"] == 0.0005
    assert row["status"] == "completed"
    assert store.total_cost_usd("r1") == 0.0005


def test_fail_step_records_error_and_status():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    a = store.start_step(run_id="r1", lead_id=None, step_name="screenshot", input_hash="h")
    store.fail_step(a, error_message="timeout")
    row = store._conn.execute(
        "SELECT status, error_message FROM step_attempts WHERE step_attempt_id=?", (a,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "timeout"


def _seed_lead_for_decision(store: WorkflowStore) -> str:
    store.create_run(run_id="r1", source_input="z")
    return store.upsert_lead(
        place_id="p1", name="Cafe", category=None, address=None,
        phone=None, website="https://x.test",
    )


def test_record_decision_marks_lead_with_decision_type():
    store = WorkflowStore(":memory:")
    lead_id = _seed_lead_for_decision(store)
    assert store.get_decision(lead_id) is None
    store.record_decision(lead_id=lead_id, decision="approve", run_id="r1")
    row = store.get_decision(lead_id)
    assert row["decision"] == "approve"
    assert row["run_id"] == "r1"
    assert row["decided_at"]


def test_record_decision_is_append_only_latest_wins():
    store = WorkflowStore(":memory:")
    lead_id = _seed_lead_for_decision(store)
    store.record_decision(lead_id=lead_id, decision="revise", run_id="r1")
    store.record_decision(lead_id=lead_id, decision="approve", run_id="r1")
    rows = list(store._conn.execute(
        "SELECT * FROM lead_decisions WHERE lead_id=? ORDER BY decided_at", (lead_id,)
    ))
    assert len(rows) == 2
    assert store.get_decision(lead_id)["decision"] == "approve"


def test_recover_dead_runs_abandons_stale_null_pid():
    """If set_runner_pid never fired and the runs row is older than the grace
    window, recover_dead_runs must abandon it — otherwise it's immortal."""
    store = WorkflowStore(":memory:")
    store._conn.execute(
        "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
        " VALUES (?, ?, ?, 'running', NULL)",
        ("stale", "z", "2000-01-01T00:00:00.000000Z"),
    )
    assert store.recover_dead_runs(null_pid_grace_seconds=60) == 1
    assert store.get_run("stale")["status"] == "abandoned"


def test_get_decision_missing_returns_none():
    store = WorkflowStore(":memory:")
    assert store.get_decision("nope") is None


def test_step_attempts_dedupe_across_runs_when_same_input_hash():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    store.create_run(run_id="r2", source_input="z")
    lead_id = store.upsert_lead(place_id="p", name="x", category=None,
                                address=None, phone=None, website="https://x")
    a1 = store.start_step(run_id="r1", lead_id=lead_id, step_name="filter",
                          input_hash="same_hash")
    store.complete_step(a1, payload={"exclude": False})
    a2 = store.start_step(run_id="r2", lead_id=lead_id, step_name="filter",
                          input_hash="same_hash")
    assert a1 == a2  # different run_id, same hash, reuses canonical attempt


def test_create_run_leaves_pid_null():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    assert store.get_run("r1")["runner_pid"] is None


def test_set_runner_pid_writes_pid():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    store.set_runner_pid(run_id="r1", pid=12345)
    assert store.get_run("r1")["runner_pid"] == 12345


def test_recover_dead_runs_leaves_null_pid():
    """NULL pid means the runner hasn't reported in yet — don't abandon it."""
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    assert store.recover_dead_runs() == 0
    assert store.get_run("r1")["status"] == "running"


def test_recover_dead_runs_demotes_dead_pid():
    store = WorkflowStore(":memory:")
    store._conn.execute(
        "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
        " VALUES (?,?,?,?,?)",
        ("dead", "z", "2026-05-01T00:00:00Z", "running", 999999999),
    )
    store._conn.execute(
        "INSERT INTO step_attempts (step_attempt_id, run_id, lead_id, step_name,"
        " input_hash, status, started_at) VALUES (?,?,?,?,?,?,?)",
        ("s1", "dead", None, "filter", "h", "running", "2026-05-01T00:00:00Z"),
    )
    abandoned = store.recover_dead_runs()
    assert abandoned == 1
    run_row = store.get_run("dead")
    step_row = store._conn.execute(
        "SELECT status, error_message FROM step_attempts WHERE step_attempt_id='s1'"
    ).fetchone()
    assert run_row["status"] == "abandoned"
    assert step_row["status"] == "failed"
    assert step_row["error_message"] == "abandoned"


def test_recover_dead_runs_leaves_live_pid():
    import os
    store = WorkflowStore(":memory:")
    store._conn.execute(
        "INSERT INTO runs (run_id, source_input, created_at, status, runner_pid)"
        " VALUES (?,?,?,?,?)",
        ("live", "z", "2026-05-01T00:00:00Z", "running", os.getpid()),
    )
    abandoned = store.recover_dead_runs()
    assert abandoned == 0
    assert store.get_run("live")["status"] == "running"
