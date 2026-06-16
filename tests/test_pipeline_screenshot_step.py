from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.pipeline import screenshot_input_hash


def test_screenshot_input_hash_is_deterministic():
    h1 = screenshot_input_hash(lead_id="L1", website="https://x.test")
    h2 = screenshot_input_hash(lead_id="L1", website="https://x.test")
    assert h1 == h2 and len(h1) == 64


def test_screenshot_input_hash_differs_when_website_changes():
    h1 = screenshot_input_hash(lead_id="L1", website="https://x.test")
    h2 = screenshot_input_hash(lead_id="L1", website="https://y.test")
    assert h1 != h2
