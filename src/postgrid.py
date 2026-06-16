"""PostGrid Print-and-Mail: send a rendered letter PDF for physical delivery.

Docs (fetched 2026-05-17):
- API overview / auth + test mode: https://postgrid.readme.io/docs/overview
- Create-letter walkthrough:        https://postgrid.readme.io/docs/sending-letters-using-the-api
- Per-letter pricing:               https://www.postgrid.com/pricing-print-mail/

Key facts the implementation relies on:
- POST https://api.postgrid.com/print-mail/v1/letters with multipart/form-data;
  attach the local PDF as the `pdf` form field.
- Auth header: `x-api-key: <key>`. Test and live use *different keys against the
  same endpoint*. Test-mode letters are never delivered, even after the key is
  swapped — pick the key intentionally.
- Idempotency: `Idempotency-Key` HTTP header (docs example also shows it as a
  body field; the header is the safer/standard choice).
- Recipient + sender both require addressLine1, city, provinceOrState,
  postalOrZip; we send `to[...]` / `from[...]` form fields.
- Response carries `id`, `status`, `live`, no cost. We record the published
  first-class B&W rate (USD 1.019) as the estimate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from pypdf import PdfReader

from src.outreach import parse_address


API_URL = "https://api.postgrid.com/print-mail/v1/letters"
# Published PostGrid pricing (pricing-print-mail/, 2026-05-17): $1.019 for the
# first color page + $0.20/extra color page. Mockup is in color so we send the
# whole packet in color and bill accordingly.
BASE_COST_USD = 1.019
EXTRA_COLOR_PAGE_USD = 0.20
HTTP_TIMEOUT = 60.0


def _estimate_cost_usd(pdf_path: Path) -> float:
    pages = len(PdfReader(str(pdf_path)).pages)
    return round(BASE_COST_USD + EXTRA_COLOR_PAGE_USD * max(0, pages - 1), 4)


def _split_city_state_zip(city_state_zip: str) -> tuple[str, str, str]:
    """Split 'Austin, TX 78704' into ('Austin', 'TX', '78704')."""
    text = (city_state_zip or "").strip().rstrip(",")
    match = re.match(r"^(.*?),\s*([A-Za-z]{2})\s+(\S+)\s*$", text)
    if match:
        return match.group(1).strip(), match.group(2).upper(), match.group(3).strip()
    return text, "", ""


def _recipient_fields(lead: dict) -> dict:
    parsed = parse_address(lead.get("address") or "")
    city, state, zip_code = _split_city_state_zip(parsed["city_state_zip"])
    if not (parsed["street"] and city and state and zip_code):
        raise ValueError(
            f"Unparseable recipient address for {lead.get('name')!r}: "
            f"{lead.get('address')!r}"
        )
    return {
        "to[companyName]": (lead.get("name") or "").strip() or "Resident",
        "to[addressLine1]": parsed["street"],
        "to[city]": city,
        "to[provinceOrState]": state,
        "to[postalOrZip]": zip_code,
        "to[country]": "US",
    }


def _sender_fields(sender: dict) -> dict:
    street = (sender.get("agency_street") or "").strip()
    city, state, zip_code = _split_city_state_zip(sender.get("agency_city_state_zip") or "")
    if not (street and city and state and zip_code):
        raise ValueError(
            "PostGrid requires a return address: set AGENCY_STREET and "
            "AGENCY_CITY_STATE_ZIP (e.g. 'Austin, TX 78704') in .env."
        )
    return {
        "from[companyName]": (sender.get("agency_name") or "").strip(),
        "from[addressLine1]": street,
        "from[city]": city,
        "from[provinceOrState]": state,
        "from[postalOrZip]": zip_code,
        "from[country]": "US",
    }


async def send_letter(
    *, lead_id: str, run_id: str, lead: dict, pdf_path: Path, sender: dict, store,
) -> dict:
    """Send `pdf_path` via PostGrid. Idempotent on (lead_id, run_id).

    Returns {provider_letter_id, status, cost_usd, mode}. Raises on failure
    after recording status='failed' on the postgrid_sends row.
    """
    mode = (os.environ.get("POSTGRID_MODE") or "test").strip().lower()
    if mode == "live" and os.environ.get("POSTGRID_LIVE") != "1":
        raise RuntimeError(
            "POSTGRID_MODE=live requires POSTGRID_LIVE=1 (explicit confirmation "
            "to spend real money). Set it in .env to actually mail."
        )

    existing = store.get_postgrid_send(lead_id, run_id)
    if existing and existing["status"] == "sent":
        return {
            "provider_letter_id": existing["provider_letter_id"],
            "status": "sent",
            "cost_usd": existing["cost_usd"],
            "mode": existing["mode"],
        }

    api_key = os.environ.get("POSTGRID_API_KEY")
    if not api_key:
        raise RuntimeError("POSTGRID_API_KEY is not set; add it to .env.")

    # Validate before inserting the pending row so a bad address doesn't leave
    # an orphaned pending row that re-clicking can never resolve.
    form = {
        **_recipient_fields(lead),
        **_sender_fields(sender),
        "addressPlacement": "top_first_page",
        "color": "true",
        "doubleSided": "false",
        "description": f"lead {lead_id} run {run_id}",
    }
    send_id = store.record_postgrid_send(lead_id=lead_id, run_id=run_id, mode=mode)
    headers = {
        "x-api-key": api_key,
        "Idempotency-Key": f"{lead_id}-{run_id}",
    }
    pdf_path = Path(pdf_path)
    estimated_cost = _estimate_cost_usd(pdf_path)

    try:
        with pdf_path.open("rb") as fh:
            files = {"pdf": (pdf_path.name, fh, "application/pdf")}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.post(API_URL, data=form, files=files, headers=headers)
    except httpx.HTTPError as error:
        message = f"{error.__class__.__name__}: {error}"
        store.update_postgrid_send(
            postgrid_send_id=send_id, status="failed", error_message=message,
        )
        raise RuntimeError(f"PostGrid network error: {message}") from error

    if response.status_code // 100 != 2:
        snippet = response.text[:300]
        store.update_postgrid_send(
            postgrid_send_id=send_id, status="failed",
            error_message=f"HTTP {response.status_code}: {snippet}",
        )
        raise RuntimeError(f"PostGrid HTTP {response.status_code}: {snippet}")

    body = response.json()
    provider_letter_id = body.get("id")
    store.update_postgrid_send(
        postgrid_send_id=send_id, status="sent",
        provider_letter_id=provider_letter_id, cost_usd=estimated_cost,
    )
    return {
        "provider_letter_id": provider_letter_id,
        "status": "sent",
        "cost_usd": estimated_cost,
        "mode": mode,
    }
