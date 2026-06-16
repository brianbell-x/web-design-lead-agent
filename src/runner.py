"""Pipeline worker subprocess. Isolated from `ui/server.py` so a crash here
can't take down the FastAPI app."""

import asyncio
import os
import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import httpx
from playwright.async_api import async_playwright

from src.core import (
    REPO_ROOT,
    ROOT_DIR,
    STATE_DB_PATH,
    TERMINAL_DECISIONS,
    LeadSkip,
    WorkflowStore,
    file_slug,
    load_env_file,
    rel,
    requested_zip_codes,
    run_dir,
    sha256_file,
    timestamp,
)
from src.pipeline import (
    capture_screenshot,
    fetch_lead_queue,
    filter_lead,
    generate_mockup,
    review_screenshot,
    screenshot_path_for,
)


def _max_run_cost_usd() -> float:
    """Per-run mockup spend ceiling in USD. -1 disables the cap. Default 10.0."""
    return float(os.environ.get("MAX_RUN_COST_USD", "10.0").strip())


def _is_finalized(decision_row) -> bool:
    """revise/regenerate are recorded for audit but don't dispose of the lead."""
    return decision_row is not None and decision_row["decision"] in TERMINAL_DECISIONS


def should_continue_within_budget(
    store: WorkflowStore, run_id: str, max_usd: float
) -> bool:
    if max_usd < 0:
        return True
    return store.total_cost_usd(run_id) < max_usd


async def process_lead(
    *,
    lead_id: str,
    browser,
    http: httpx.AsyncClient,
    screenshot_sem: asyncio.Semaphore,
    openai_text_sem: asyncio.Semaphore,
    mockup_sem: asyncio.Semaphore,
    store: WorkflowStore,
    run_id: str,
    run_dir_root,
) -> bool:
    lead_row = store.get_lead(lead_id)
    print(f"Checking: {lead_row['name']} ({lead_row['website']})")
    lead_dir = screenshot_path_for(run_dir_root, lead_row).parent

    try:
        async with screenshot_sem:
            screenshot = await capture_screenshot(
                browser, lead_row, run_id, run_dir_root, store
            )
        sha = sha256_file(screenshot["path"])

        async with openai_text_sem:
            decision = await filter_lead(
                http, lead_row, screenshot["path"], sha, run_id, store
            )
        if decision["exclude"]:
            print(f"  excluded: {decision['exclude_reason']}")
            return False

        async with openai_text_sem:
            review = await review_screenshot(
                http, lead_row, screenshot["path"], sha, lead_dir, run_id, store
            )
        payload = review["payload"]
        if not payload["needs_redesign"]:
            print(f"  no redesign: {payload['reasoning']}")
            return False

        if not should_continue_within_budget(store, run_id, _max_run_cost_usd()):
            print("  skipped: cost cap reached")
            return False

        async with mockup_sem:
            _, mockup_path = await generate_mockup(
                http,
                lead_row,
                screenshot["path"],
                sha,
                payload,
                lead_dir,
                run_id,
                store,
            )
        if not mockup_path:
            raise LeadSkip("mockup generator returned no image")
        print(f"  mockup: {rel(mockup_path)}")
        return True

    except LeadSkip as error:
        print(f"  skipped: {error}")
        return False


async def main() -> None:
    load_env_file(ROOT_DIR / ".env")
    load_env_file(REPO_ROOT / ".env")

    source_label, zip_codes = requested_zip_codes()
    run_id = f"{file_slug(source_label)}_{timestamp()}"
    print(f"Run: {run_id}")
    print(f"Run files: {rel(run_dir(run_id))}")
    if len(zip_codes) > 1:
        print(f"Queued {len(zip_codes)} ZIP codes for {source_label}.")

    store = WorkflowStore(STATE_DB_PATH)
    recovered = store.recover_dead_runs()
    if recovered:
        print(f"Recovered {recovered} dead run(s).")
    store.create_run(run_id=run_id, source_input=source_label)
    store.set_runner_pid(run_id=run_id, pid=os.getpid())
    run_dir_root = run_dir(run_id)
    run_dir_root.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as http:
        lead_ids = await fetch_lead_queue(http, zip_codes, run_id, store)
        if not lead_ids:
            store.complete_run(run_id, status="no_leads")
            raise RuntimeError(
                f"No website-bearing businesses found for {source_label}."
            )

        previously_decided = [lid for lid in lead_ids if _is_finalized(store.get_decision(lid))]
        lead_ids = [lid for lid in lead_ids if not _is_finalized(store.get_decision(lid))]
        if previously_decided:
            print(f"Skipping {len(previously_decided)} leads with prior decisions.")
        print(f"Processing {len(lead_ids)} leads.")
        if not lead_ids:
            store.complete_run(run_id, status="no_needs_redesign_lead_found")
            print("All leads have prior decisions; nothing to process.")
            return

        screenshot_sem = asyncio.Semaphore(5)
        openai_text_sem = asyncio.Semaphore(3)
        mockup_sem = asyncio.Semaphore(2)

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    results = await asyncio.gather(
                        *(
                            process_lead(
                                lead_id=lid,
                                browser=browser,
                                http=http,
                                screenshot_sem=screenshot_sem,
                                openai_text_sem=openai_text_sem,
                                mockup_sem=mockup_sem,
                                store=store,
                                run_id=run_id,
                                run_dir_root=run_dir_root,
                            )
                            for lid in lead_ids
                        ),
                        return_exceptions=True,
                    )
                finally:
                    await browser.close()
        except BaseException:
            store.complete_run(run_id, status="failed")
            raise

    failed = sum(1 for r in results if isinstance(r, BaseException))
    qualified = sum(1 for r in results if r is True)
    max_usd = _max_run_cost_usd()
    if max_usd >= 0 and store.total_cost_usd(run_id) >= max_usd:
        status = "cost_capped"
    elif qualified > 0:
        status = "completed"
    else:
        status = "no_needs_redesign_lead_found"
    store.complete_run(run_id, status=status)
    total_cost = store.total_cost_usd(run_id)
    print("")
    print(f"Qualified leads: {qualified}/{len(lead_ids)}")
    if failed:
        import traceback

        first_exc = next(r for r in results if isinstance(r, BaseException))
        print(f"Unexpected errors: {failed}")
        traceback.print_exception(type(first_exc), first_exc, first_exc.__traceback__)
    print(f"Run cost: ${total_cost:.4f}")
    print(f"Run id: {run_id}")


if __name__ == "__main__":
    asyncio.run(main())
