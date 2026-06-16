import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.core import WorkflowStore
from src.postgrid import BASE_COST_USD, EXTRA_COLOR_PAGE_USD, send_letter


def _make_pdf(path: Path, pages: int = 3) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


SENDER = {
    "agency_name": "Studio",
    "agency_email": "x@y.test",
    "signature_name": "X",
    "agency_phone": "555-0100",
    "agency_street": "1 Main St",
    "agency_city_state_zip": "Austin, TX 78704",
}
LEAD = {"name": "Cafe Sol", "address": "200 Maple Ave, Austin, TX 78704"}


def _seed(store: WorkflowStore) -> tuple[str, str]:
    store.create_run(run_id="r1", source_input="z")
    lead_id = store.upsert_lead(
        place_id="p1", name="Cafe Sol", category=None,
        address="200 Maple Ave, Austin, TX 78704", phone=None, website="https://x.test",
    )
    return "r1", lead_id


def _mock_post(status_code: int, body: dict | None = None, text: str = "") -> MagicMock:
    """Build a patch object whose async __aenter__ yields a client with mocked .post()."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = body or {}
    response.text = text or (str(body) if body else "")
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return client, context


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("POSTGRID_API_KEY", "test_sk_abc")
    monkeypatch.setenv("POSTGRID_MODE", "test")
    monkeypatch.delenv("POSTGRID_LIVE", raising=False)
    yield monkeypatch


def test_send_letter_test_mode_records_send(env, tmp_path):
    pdf = _make_pdf(tmp_path / "outreach.pdf", pages=3)
    store = WorkflowStore(":memory:")
    run_id, lead_id = _seed(store)
    _, ctx = _mock_post(200, {"id": "letter_abc123", "status": "ready", "live": False})

    with patch("src.postgrid.httpx.AsyncClient", return_value=ctx):
        result = asyncio.run(send_letter(
            lead_id=lead_id, run_id=run_id, lead=LEAD,
            pdf_path=pdf, sender=SENDER, store=store,
        ))

    assert result["provider_letter_id"] == "letter_abc123"
    row = store.get_postgrid_send(lead_id, run_id)
    assert row["status"] == "sent"
    assert row["mode"] == "test"
    assert row["provider_letter_id"] == "letter_abc123"
    assert row["cost_usd"] == round(BASE_COST_USD + 2 * EXTRA_COLOR_PAGE_USD, 4)


def test_send_letter_refuses_live_without_explicit_flag(env, tmp_path):
    env.setenv("POSTGRID_MODE", "live")
    pdf = _make_pdf(tmp_path / "outreach.pdf")
    store = WorkflowStore(":memory:")
    run_id, lead_id = _seed(store)
    client, ctx = _mock_post(200, {"id": "x"})

    with patch("src.postgrid.httpx.AsyncClient", return_value=ctx):
        with pytest.raises(RuntimeError, match="POSTGRID_LIVE=1"):
            asyncio.run(send_letter(
                lead_id=lead_id, run_id=run_id, lead=LEAD,
                pdf_path=pdf, sender=SENDER, store=store,
            ))
    client.post.assert_not_called()


def test_send_letter_is_idempotent_per_lead_run(env, tmp_path):
    pdf = _make_pdf(tmp_path / "outreach.pdf")
    store = WorkflowStore(":memory:")
    run_id, lead_id = _seed(store)
    client, ctx = _mock_post(200, {"id": "letter_xyz", "status": "ready"})

    with patch("src.postgrid.httpx.AsyncClient", return_value=ctx):
        first = asyncio.run(send_letter(
            lead_id=lead_id, run_id=run_id, lead=LEAD,
            pdf_path=pdf, sender=SENDER, store=store,
        ))
        second = asyncio.run(send_letter(
            lead_id=lead_id, run_id=run_id, lead=LEAD,
            pdf_path=pdf, sender=SENDER, store=store,
        ))

    assert first["provider_letter_id"] == second["provider_letter_id"] == "letter_xyz"
    assert client.post.call_count == 1  # second call short-circuited


def test_send_letter_records_failure_on_http_error(env, tmp_path):
    pdf = _make_pdf(tmp_path / "outreach.pdf")
    store = WorkflowStore(":memory:")
    run_id, lead_id = _seed(store)
    _, ctx = _mock_post(500, text="server boom")

    with patch("src.postgrid.httpx.AsyncClient", return_value=ctx):
        with pytest.raises(RuntimeError, match="500"):
            asyncio.run(send_letter(
                lead_id=lead_id, run_id=run_id, lead=LEAD,
                pdf_path=pdf, sender=SENDER, store=store,
            ))
    row = store.get_postgrid_send(lead_id, run_id)
    assert row["status"] == "failed"
    assert "500" in row["error_message"]


def test_total_cost_usd_includes_postgrid_sends():
    store = WorkflowStore(":memory:")
    store.create_run(run_id="r1", source_input="z")
    lead_id = store.upsert_lead(
        place_id="p", name="x", category=None, address=None, phone=None,
        website="https://x",
    )
    a = store.start_step(run_id="r1", lead_id=lead_id, step_name="filter", input_hash="h")
    store.complete_step(a, payload={"ok": True}, cost_usd=0.5)
    send_id = store.record_postgrid_send(lead_id=lead_id, run_id="r1", mode="test")
    store.update_postgrid_send(
        postgrid_send_id=send_id, status="sent",
        provider_letter_id="letter_1", cost_usd=1.20,
    )
    assert store.total_cost_usd("r1") == 1.70
