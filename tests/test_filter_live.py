"""Live smoke test for filter_lead.

Hits OpenAI Responses with FILTER_MODEL. Verifies the cheap filter model
accepts image input + json_schema output and returns a usable decision.

Usage:
    python -u tests/test_filter_live.py [url]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from playwright.async_api import async_playwright

from src.core import REPO_ROOT, ROOT_DIR, load_env_file, rel, timestamp
from src.pipeline import FILTER_MODEL, capture_screenshot, filter_lead


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.mcdonalds.com"
    load_env_file(ROOT_DIR / ".env")
    load_env_file(REPO_ROOT / ".env")

    run_id = f"filter_test_{timestamp()}"
    from src.core import STATE_DB_PATH, WorkflowStore, sha256_file

    store = WorkflowStore(STATE_DB_PATH)
    store.create_run(run_id=run_id, source_input=url)
    lead_id = store.upsert_lead(
        place_id=f"test_{url}", name=url.split("//")[-1].split("/")[0],
        category="test", address=None, phone=None, website=url,
    )
    store.link_lead_to_run(run_id=run_id, lead_id=lead_id, ordinal=1)
    lead_row = store.get_lead(lead_id)

    print(f"URL: {url}", flush=True)
    print(f"Model: {FILTER_MODEL}", flush=True)
    async with httpx.AsyncClient() as http, async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            from src.core import run_dir
            screenshot = await capture_screenshot(browser, lead_row, run_id, run_dir(run_id), store)
        finally:
            await browser.close()
        print(f"Screenshot: {rel(screenshot['path'])}", flush=True)

        decision = await filter_lead(http, lead_row, screenshot["path"], sha256_file(screenshot["path"]), run_id, store)
    print(f"exclude: {decision['exclude']}", flush=True)
    print(f"reason:  {decision['exclude_reason'] or '(none)'}", flush=True)
    print("OK", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
