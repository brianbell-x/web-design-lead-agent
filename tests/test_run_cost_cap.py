from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.core import WorkflowStore


def _seed_cost(store: WorkflowStore, run_id: str, lead_id: str, dollars: float) -> None:
    a = store.start_step(run_id=run_id, lead_id=lead_id, step_name="mockup",
                         input_hash=f"h_{lead_id}")
    store.complete_step(a, payload={}, cost_usd=dollars)


def _store_with_lead() -> tuple[WorkflowStore, str]:
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    lead_id = store.upsert_lead(place_id="p", name="x", category=None,
                                address=None, phone=None, website="https://x")
    return store, lead_id


def test_cost_cap_disabled_when_negative():
    from src.runner import should_continue_within_budget
    store, lead_id = _store_with_lead()
    _seed_cost(store, "r1", lead_id, 99.0)
    assert should_continue_within_budget(store, "r1", max_usd=-1.0) is True


def test_cost_cap_blocks_when_exceeded():
    from src.runner import should_continue_within_budget
    store, lead_id = _store_with_lead()
    _seed_cost(store, "r1", lead_id, 5.50)
    assert should_continue_within_budget(store, "r1", max_usd=5.00) is False


def test_cost_cap_allows_when_under():
    from src.runner import should_continue_within_budget
    store, lead_id = _store_with_lead()
    _seed_cost(store, "r1", lead_id, 1.00)
    assert should_continue_within_budget(store, "r1", max_usd=5.00) is True


def test_max_run_cost_usd_reads_env(monkeypatch):
    from src.runner import _max_run_cost_usd
    monkeypatch.setenv("MAX_RUN_COST_USD", "3.50")
    assert _max_run_cost_usd() == 3.50


def test_max_run_cost_usd_default_when_unset(monkeypatch):
    from src.runner import _max_run_cost_usd
    monkeypatch.delenv("MAX_RUN_COST_USD", raising=False)
    assert _max_run_cost_usd() == 10.0
