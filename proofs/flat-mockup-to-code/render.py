from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 853}, device_scale_factor=1)
    page.goto(root.joinpath("index.html").as_uri())
    page.screenshot(path=root / "render.png")
    browser.close()
