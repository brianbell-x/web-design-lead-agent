from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.outreach import render_outreach_html


def _tiny_png(path: Path) -> None:
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c63000100000005000110d10b6e0000000049454e44ae426082"
    ))


def test_business_name_with_html_chars_is_escaped(tmp_path):
    mockup = tmp_path / "mockup.png"
    _tiny_png(mockup)
    lead = {"name": "Joe & <script>alert(1)</script> Co",
            "address": "1 Main, Austin, TX 78704"}
    html_path = tmp_path / "out.html"
    render_outreach_html(lead=lead, mockup_path=mockup, output_html_path=html_path)
    rendered = html_path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in rendered
    assert "Joe &amp; &lt;script&gt;alert(1)&lt;/script&gt; Co" in rendered


def test_address_with_ampersand_is_escaped(tmp_path):
    mockup = tmp_path / "mockup.png"
    _tiny_png(mockup)
    lead = {"name": "Smith Plumbing",
            "address": "123 Main St & Oak Ave, Austin, TX 78704"}
    html_path = tmp_path / "out.html"
    render_outreach_html(lead=lead, mockup_path=mockup, output_html_path=html_path)
    rendered = html_path.read_text(encoding="utf-8")
    assert "& Oak" not in rendered
    assert "&amp; Oak" in rendered
