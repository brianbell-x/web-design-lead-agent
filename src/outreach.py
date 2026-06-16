"""Render a 3-page outreach packet (address -> letter -> mockup) to PDF.

Page sizes are driven by CSS @page rules; the mockup page is sized to the
image's pixel dimensions converted at 96 DPI so the page is cropped exactly
to the image with no letterboxing or stretching.
"""

import base64
import html
import re
from datetime import date
from pathlib import Path
from string import Template

from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter


TEMPLATE_PATH = Path(__file__).resolve().parent / "outreach_template.html"
PAGE_DPI = 96  # CSS reference DPI; 1 CSS px = 1/96 in

DEFAULT_AGENCY_NAME = "Insert Agency Name"
DEFAULT_AGENCY_PHONE = "Insert Phone Number"
DEFAULT_AGENCY_EMAIL = "insert@email.com"
DEFAULT_SIGNATURE_NAME = "Insert Your Name"
DEFAULT_AGENCY_STREET = ""
DEFAULT_AGENCY_CITY_STATE_ZIP = ""


def image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def image_data_url(path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"


def parse_address(full_address: str) -> dict:
    cleaned = re.sub(r",\s*USA\s*$", "", (full_address or "").strip(), flags=re.IGNORECASE).rstrip(",")
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        city_state_zip = f"{parts[1]}, {', '.join(parts[2:])}"
    elif len(parts) == 2:
        street, city = parts[0], parts[1]
        city_state_zip = parts[1]
    else:
        street, city, city_state_zip = cleaned, "", ""
    return {"street": street, "city": city, "city_state_zip": city_state_zip}


def render_outreach_html(
    *,
    lead: dict,
    mockup_path: Path,
    output_html_path: Path,
    agency_name: str = DEFAULT_AGENCY_NAME,
    agency_phone: str = DEFAULT_AGENCY_PHONE,
    agency_email: str = DEFAULT_AGENCY_EMAIL,
    signature_name: str = DEFAULT_SIGNATURE_NAME,
    agency_street: str = DEFAULT_AGENCY_STREET,
    agency_city_state_zip: str = DEFAULT_AGENCY_CITY_STATE_ZIP,
    letter_date: date | None = None,
) -> dict:
    address = parse_address(lead.get("address") or "")
    width_px, height_px = image_dimensions(Path(mockup_path))
    width_in = width_px / PAGE_DPI
    height_in = height_px / PAGE_DPI

    today = letter_date or date.today()
    formatted_date = f"{today.strftime('%B')} {today.day}, {today.year}"

    phone = (agency_phone or "").strip()

    def _esc(value: str | None) -> str:
        return html.escape(value or "", quote=True)

    business_name_esc = _esc((lead.get("name") or "").strip() or "Friend")
    business_street_esc = _esc(address["street"] or "")
    business_city_state_zip_esc = _esc(address["city_state_zip"] or "")
    city_only_esc = _esc(address["city"] or "your area")
    agency_name_esc = _esc(agency_name)
    agency_phone_esc = _esc(phone)
    agency_email_esc = _esc(agency_email)
    signature_name_esc = _esc(signature_name)
    contact_phrase = f"{agency_phone_esc}, or {agency_email_esc}" if phone else agency_email_esc
    contact_line = f"{agency_phone_esc} &nbsp;·&nbsp; {agency_email_esc}" if phone else agency_email_esc
    street = (agency_street or "").strip()
    city_state_zip = (agency_city_state_zip or "").strip()
    if street or city_state_zip:
        line = ", ".join(p for p in (_esc(street), _esc(city_state_zip)) if p)
        agency_return_address_block = f'<div class="return-address">{agency_name_esc}<br>{line}</div>'
    else:
        agency_return_address_block = ""

    rendered = Template(TEMPLATE_PATH.read_text(encoding="utf-8")).safe_substitute(
        business_name=business_name_esc,
        business_street=business_street_esc,
        business_city_state_zip=business_city_state_zip_esc,
        city_only=city_only_esc,
        letter_date=formatted_date,
        agency_name=agency_name_esc,
        agency_phone=agency_phone_esc,
        agency_email=agency_email_esc,
        signature_name=signature_name_esc,
        contact_phrase=contact_phrase,
        contact_line=contact_line,
        agency_return_address_block=agency_return_address_block,
        mockup_src=image_data_url(mockup_path),
        mockup_width=f"{width_in:.4f}in",
        mockup_height=f"{height_in:.4f}in",
    )

    Path(output_html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_html_path).write_text(rendered, encoding="utf-8")
    return {
        "html_path": Path(output_html_path),
        "mockup_pixels": (width_px, height_px),
        "mockup_inches": (round(width_in, 4), round(height_in, 4)),
        "letter_date": formatted_date,
    }


def render_outreach_pdf(
    *,
    lead: dict,
    mockup_path: Path,
    output_dir: Path,
    agency_name: str = DEFAULT_AGENCY_NAME,
    agency_phone: str = DEFAULT_AGENCY_PHONE,
    agency_email: str = DEFAULT_AGENCY_EMAIL,
    signature_name: str = DEFAULT_SIGNATURE_NAME,
    agency_street: str = DEFAULT_AGENCY_STREET,
    agency_city_state_zip: str = DEFAULT_AGENCY_CITY_STATE_ZIP,
    letter_date: date | None = None,
) -> dict:
    """Render a 3-page outreach PDF.

    Chromium's PDF engine does not honor `@page` named-page sizes mixed in a
    single render, so the address+letter and the mockup are rendered as two
    separate PDFs (each with its own page size) and merged with pypdf.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "outreach.html"
    pdf_path = output_dir / "outreach.pdf"
    letter_pdf = output_dir / "_outreach_letter.pdf"
    mockup_pdf = output_dir / "_outreach_mockup.pdf"

    rendered = render_outreach_html(
        lead=lead,
        mockup_path=mockup_path,
        output_html_path=html_path,
        agency_name=agency_name,
        agency_phone=agency_phone,
        agency_email=agency_email,
        signature_name=signature_name,
        agency_street=agency_street,
        agency_city_state_zip=agency_city_state_zip,
        letter_date=letter_date,
    )
    width_in, height_in = rendered["mockup_inches"]

    no_margin = {"top": "0", "bottom": "0", "left": "0", "right": "0"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_context().new_page()
            page.goto(html_path.as_uri(), wait_until="networkidle")
            page.evaluate("document.documentElement.classList.add('render-letter')")
            page.pdf(path=str(letter_pdf), format="Letter", margin=no_margin, print_background=True)
            page.evaluate("document.documentElement.classList.replace('render-letter', 'render-mockup')")
            page.pdf(path=str(mockup_pdf), width=f"{width_in:.4f}in",
                     height=f"{height_in:.4f}in", margin=no_margin, print_background=True)
            page.context.close()
        finally:
            browser.close()

    writer = PdfWriter()
    for source in (letter_pdf, mockup_pdf):
        for page_obj in PdfReader(str(source)).pages:
            writer.add_page(page_obj)
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    letter_pdf.unlink(missing_ok=True)
    mockup_pdf.unlink(missing_ok=True)

    return {
        "html_path": html_path,
        "pdf_path": pdf_path,
        "mockup_pixels": rendered["mockup_pixels"],
        "mockup_inches": rendered["mockup_inches"],
        "letter_date": rendered["letter_date"],
    }


def render_outreach_for_lead(lead_id: str, run_id: str, store, output_dir: Path,
                             **render_kwargs) -> dict:
    """Render outreach for a stored lead using its latest mockup artifact, and record the PDF."""
    lead = dict(store.get_lead(lead_id))
    mockup = store._conn.execute(
        "SELECT path FROM artifacts WHERE run_id=? AND lead_id=? AND kind='mockup'"
        " ORDER BY created_at DESC LIMIT 1",
        (run_id, lead_id),
    ).fetchone()
    if not mockup:
        raise RuntimeError(f"No mockup artifact for lead {lead_id} in run {run_id}.")
    result = render_outreach_pdf(
        lead=lead, mockup_path=Path(mockup["path"]), output_dir=output_dir,
        **render_kwargs,
    )
    store.record_artifact(
        run_id=run_id, lead_id=lead_id, kind="outreach_pdf", path=result["pdf_path"],
        metadata={"mockup_inches": result["mockup_inches"], "letter_date": result["letter_date"]},
    )
    return result
