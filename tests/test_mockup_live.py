"""Live smoke test for review_screenshot using OpenAI Responses API for the
review (returning business_description + vibe) followed by a second Responses
call with the image_generation tool (gpt-image-2) for the mockup. Verifies
the two-call flow end-to-end.

The McDonald's screenshot legitimately reviews as needs_redesign=false,
so this test is parameterized: pass any URL or a screenshot path. With
no args it falls back to a known-dated demo site to exercise the
mockup generation path end-to-end.

Usage:
    python -u tests/test_mockup_live.py                      # captures fresh screenshot of DEMO_URL
    python -u tests/test_mockup_live.py --screenshot PATH    # reuses an existing screenshot
    python -u tests/test_mockup_live.py URL                  # captures fresh screenshot of URL
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from playwright.async_api import async_playwright

from src.core import REPO_ROOT, ROOT_DIR, load_env_file, rel, timestamp
from src.pipeline import REVIEW_MODEL, capture_screenshot, generate_mockup, review_screenshot


DEMO_URL = "https://www.lingscars.com/"


async def main() -> None:
    load_env_file(ROOT_DIR / ".env")
    load_env_file(REPO_ROOT / ".env")

    args = sys.argv[1:]
    screenshot_arg = None
    url = DEMO_URL
    if args:
        if args[0] == "--screenshot" and len(args) > 1:
            screenshot_arg = Path(args[1])
        else:
            url = args[0]

    run_id = f"mockup_test_{timestamp()}"
    from src.core import STATE_DB_PATH, WorkflowStore, run_dir, sha256_file

    store = WorkflowStore(STATE_DB_PATH)
    store.create_run(run_id=run_id, source_input=url)
    lead_id = store.upsert_lead(
        place_id=f"test_{run_id}", name="mockup-smoke-test", category="test",
        address=None, phone=None, website=url,
    )
    store.link_lead_to_run(run_id=run_id, lead_id=lead_id, ordinal=1)
    lead_row = store.get_lead(lead_id)
    run_dir_root = run_dir(run_id)

    async with httpx.AsyncClient() as http:
        if screenshot_arg:
            if not screenshot_arg.exists():
                raise RuntimeError(f"Screenshot not found: {screenshot_arg}")
            screenshot_path = screenshot_arg
            print(f"Reusing screenshot: {rel(screenshot_path)}", flush=True)
        else:
            print(f"Capturing screenshot of {url}...", flush=True)
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    screenshot = await capture_screenshot(browser, lead_row, run_id, run_dir_root, store)
                finally:
                    await browser.close()
            screenshot_path = screenshot["path"]
            print(f"Screenshot: {rel(screenshot_path)}", flush=True)

        print(f"Calling review_screenshot ({REVIEW_MODEL} review + gpt-image-2 via Responses tool)...", flush=True)
        lead_dir = screenshot_path.parent
        sha = sha256_file(screenshot_path)
        result = await review_screenshot(http, lead_row, screenshot_path, sha, lead_dir, run_id, store)
        review = result["payload"]
        mockup_path = None
        if review["needs_redesign"]:
            _, mockup_path = await generate_mockup(http, lead_row, screenshot_path, sha, review, lead_dir, run_id, store)

    print(f"needs_redesign: {review['needs_redesign']}", flush=True)
    print(f"reasoning:      {review['reasoning']}", flush=True)
    if review["needs_redesign"]:
        if not mockup_path or not mockup_path.exists():
            raise RuntimeError("needs_redesign=true but no mockup file was written.")
        size = mockup_path.stat().st_size
        print(f"mockup:         {rel(mockup_path)} ({size} bytes)", flush=True)
        if size < 1000:
            raise RuntimeError(f"Mockup too small ({size} bytes), likely not a real image.")
    else:
        print("needs_redesign=false — no mockup expected. Try a different URL to exercise gpt-image-2.", flush=True)
    print("OK", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
