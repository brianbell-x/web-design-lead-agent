from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from src import core
from src.core import WorkflowStore


def test_requested_zip_codes_accepts_positional_zip(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["src.runner", "38107"])

    source_label, zip_codes = core.requested_zip_codes()

    assert source_label == "38107"
    assert zip_codes == ["38107"]


def _seed_two_leads(store: WorkflowStore, run_id: str) -> tuple[str, str]:
    store.create_run(run_id=run_id, source_input="z")
    a = store.upsert_lead(place_id="pa", name="A", category=None, address=None,
                          phone=None, website="https://a.test")
    b = store.upsert_lead(place_id="pb", name="B", category=None, address=None,
                          phone=None, website="https://b.test")
    store.link_lead_to_run(run_id=run_id, lead_id=a, ordinal=1)
    store.link_lead_to_run(run_id=run_id, lead_id=b, ordinal=2)
    return a, b


def test_decisions_filter_skips_previously_decided_leads():
    store = WorkflowStore(":memory:")
    a, b = _seed_two_leads(store, "r1")
    store.record_decision(lead_id=a, decision="approve", run_id="r1")
    queue = [a, b]
    remaining = [lid for lid in queue if store.get_decision(lid) is None]
    assert remaining == [b]


def test_decisions_keep_all_when_none_decided():
    store = WorkflowStore(":memory:")
    a, b = _seed_two_leads(store, "r1")
    queue = [a, b]
    remaining = [lid for lid in queue if store.get_decision(lid) is None]
    assert remaining == [a, b]
