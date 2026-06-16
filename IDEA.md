# Web Design Lead Agent

## Opportunity

Build a lead-generation workflow for finding local small businesses with weak websites, creating a concrete redesign mockup, and preparing an outreach packet the operator can review before sending.

The first user is the builder. The goal is to create web design work, not to sell a SaaS yet. The first version should help one operator turn one ZIP code into a reviewed list of businesses worth contacting.

This is an AgentFactory candidate, but not a free-running agent. The right shape is a workflow with a few AI judgment and generation steps, plus human approval before any outreach.

## Current State

- Google Places is the v1 lead source.
- ZIP-only search is the default starting input. A vertical/category can be added later.
- The working lead list stays simple: business identity, address, phone, website, lat/lng, status, artifact paths, notes.
- Playwright captures full-page site screenshots locally.
- OpenAI `gpt-5.4-nano-2026-03-17` runs first as a cheap multimodal pre-filter that decides exclusion only (non-business, chain, parked, error page, unsafe/adult, prompt-injection attempts) with structured output: `exclude`, `exclude_reason`. About $0.0005 per call, roughly 80x cheaper than the review step, so excluded leads never burn a gpt-5.5 call.
- OpenRouter `openai/gpt-5.5` then reviews the surviving screenshots with high reasoning effort and structured output: `needs_redesign`, `reasoning`, `business_description`, and `vibe`.
- Mockups are generated as a separate Responses API call (`gpt-5.5` wrapper + `image_generation` tool, `tool_choice` forced) with `gpt-image-2` at quality=high, size=auto. `auto` lets gpt-image-2 pick dimensions per business so the layout isn't forced into a fixed aspect ratio. The screenshot is passed as `input_image` so it acts as a brand reference (logo, palette, voice) rather than a layout to preserve, and the wrapper instructions tell the model to forward the recipe prompt verbatim: `Create a polished website home for {business_description}. Vibe is {vibe}. For Branding Purposes the orginal site is included as reference.` `/v1/images/edits` was rejected because it produced polished copies of the source layout instead of fresh redesigns; `/v1/images/generations` was rejected because it discarded the brand entirely.
- Workflow state lives in a SQLite database at `data/state/agent.db` as the source of truth: tables `runs`, `leads`, `run_leads`, `artifacts`, `step_attempts`, `provider_calls`, `lead_decisions`. Step idempotency is keyed on `(lead_id, step_name, input_hash)` so re-runs of the same prompt+screenshot+model short-circuit across runs. Per-call cost is captured on `provider_calls.cost_usd`.
- A per-run spending cap is enforced via `MAX_RUN_COST_USD` (default `10.0`); set to `-1` to disable. Checked before each mockup dispatch.
- Disk artifacts under `data/runs/{run_id}/` keep one folder per lead and hold only files the operator actually wants to look at: `source_google_places.json` (raw vendor receipt), `screenshot.jpg`, `mockup.png`, `outreach.pdf`. Every disk artifact is registered as a row in the `artifacts` table with sha256 + bytes + metadata.
- The legacy per-step JSON files (`lead.json`, `lead_filter.json`, `site_review.json`, `mockup_prompt.txt`, `packet.json`, `run_summary.json`, `error.json`) are no longer written. The DB is the source of truth; rebuild any view directly from `sqlite3 data/state/agent.db`.
- The next human step is to review the original site screenshot vs the generated mockup. That is noted but skipped for now.
- Outreach packet is rendered on demand as a 3-page PDF per lead (`outreach.pdf`) via `render_outreach_for_lead(lead_id, run_id, store, output_dir)`: page 1 is the recipient address positioned for a #10 right-window envelope, page 2 is a single-column letter on Letter-size paper, and page 3 is the mockup on a page sized exactly to the image so it is not letterboxed or stretched. The HTML source (`outreach.html`) is saved alongside the PDF for review and re-rendering. The runner no longer renders outreach in-loop after the mockup; rendering is invoked separately so a human review can sit between mockup and PDF. Letter copy is a static template with `Insert Agency Name`, `Insert Phone Number`, `insert@email.com`, and `Insert Your Name` placeholders; the template is in `src/outreach_template.html` and the renderer is `src/outreach_packet.py` (Playwright print → pypdf merge, since Chromium does not honor mixed `@page` sizes in a single render).
- Outreach ends with a physical letter path. PostGrid is the first API candidate, but it cannot be tested right now.
- The operator shell is a local FastAPI app at `ui/server.py` that surfaces the next pending lead, an SSE ticker of run events, and approve/skip controls. It is the single user-facing entry point: `python -m ui.server`. The pipeline worker (`src/runner.py`) is launched as a subprocess by the UI when the operator submits a location and is not intended to be run directly. OpenClaw is not used for this app; the workflow is too simple to justify an agent runtime.

## Commercial Value

The commercially useful output is a reviewed lead packet for a local business that may need a redesign.

The packet enables a concrete downstream action: the operator can approve outreach, revise the packet, skip the lead, or queue follow-up.

The packet should include:

- business name, category, address, phone, website, and source receipt
- current site screenshot
- redesign yes/no review with evidence
- generated redesign mockup
- operator review status
- outreach draft or printable letter proof
- send status and audit record if outreach happens

Who benefits:

- The builder gets a faster way to find and qualify local redesign prospects.
- The business owner receives a specific visual idea instead of a generic pitch.
- The workflow is valuable if it produces better leads and more compelling outreach with less manual prospecting time.

## Tool Harness

| Tool or Step | What It Does | Output | Readiness | Action Risk | Proof / Limits / Next |
| --- | --- | --- | --- | --- | --- |
| ZIP input and geocoding | Turns a ZIP into a search center/area | Search seed and geometry | Verified for one ZIP | Read-only | ZIP `78704` geocoded successfully. ZIP boundaries are imperfect, so this is lead discovery, not complete enumeration. |
| Google Places lead fetch | Finds local businesses and basic data | Business list with website-bearing candidates | Verified for a narrow smoke test; Path to test for broad harvest | Read-only | One live run returned 40 raw rows, 40 unique Place IDs, and 27 website-bearing businesses. Need broad ZIP harvest with grid/rank/type passes and cost tracking. |
| Large-list acquisition | Expands ZIP-only search into controlled Places calls | Larger candidate pool and pass-level yield | Path to test | Read-only | Strategy is area tiling, rank repeats, curated type sweeps, Text Search supplements, and strict dedupe. Need one dense ZIP run. |
| Dedupe and filtering | Removes duplicate/irrelevant/closed candidates | Clean lead list | Path to test | Internal write | Place ID and website-domain counts were inspected on one run. Need batch validation against manual review. |
| Website resolver | Confirms the listed website is the business site | Canonical website URL or skip reason | Path to test | Read-only | Places website fields can be stale, missing, or point to social/directories. Need sample verification. |
| Workflow store | SQLite DB (`data/state/agent.db`) is the source of truth for runs, leads, step attempts, artifacts, provider calls, and lead decisions; disk holds screenshots, mockups, PDFs, and raw vendor receipts. | `runs/leads/run_leads/artifacts/step_attempts/provider_calls/lead_decisions` tables + per-lead disk folder | Verified end-to-end with store unit tests | Internal write | Idempotent step replay via `(lead_id, step_name, input_hash)` unique index. Per-call cost lives on `provider_calls.cost_usd`. The DB is queryable directly with `sqlite3` for ad-hoc reporting. |
| Screenshotter | Captures current website evidence | Full-page screenshot and failure reason | Verified for narrow local runs | Read-only | Local Playwright captured batches in ZIP `38107` and `78704`, and migrated screenshots are stored per lead. Need 20-site and 100-site reliability checks with failure-rate tracking. |
| Cheap pre-filter (`gpt-5.4-nano-2026-03-17`) | Excludes leads that are not usable local-business prospects, have unusable screenshot evidence, or contain unsafe/adversarial content (adult, illegal, visible prompt-injection attempts) before the expensive review | `step_attempts` row (step_name=`filter`) with `payload_json={exclude, exclude_reason}` + `provider_calls` row with cost | Verified for one live run | Draft-only | McDonald's correctly excluded as "large national chain" at ~$0.0005 per call vs ~$0.04 for the gpt-5.5 review. Need calibration against 10 to 20 mixed leads to confirm it does not over-exclude legitimate small businesses. |
| Site redesign reviewer and slot writer | Judges whether a site is worth a redesign mockup and fills the recipe slots when yes (only runs on leads that pass the filter) | `step_attempts` row (step_name=`review`) with `payload_json={needs_redesign, reasoning, business_description, vibe}` + `provider_calls` row | Verified for narrow local runs; Path to test for calibration | Draft-only | Need 10 to 20 reviewed sites compared with human judgment before trusting scoring. |
| Static site checks | Adds objective support signals | Speed/mobile/SSL/CTA/accessibility evidence | Path to test | Read-only | Not run yet. Useful as support evidence, not as the final decision. |
| Mockup image generator | Calls Responses API with `gpt-5.5` wrapper + `image_generation` tool (forced via `tool_choice`), `gpt-image-2` at quality=high, size=auto. Screenshot passed as `input_image` for brand reference; wrapper instructions tell the model to forward the recipe prompt verbatim. Recipe is built deterministically from the review's `business_description` + `vibe` slots. | Saved PNG mockup + `step_attempts` row (step_name=`mockup`) + `provider_calls` row | Verified end-to-end on lingscars.com producing a genuine redesign (kept brand, rebuilt layout) | Draft-only | Recipe prompt: `Create a polished website home for {business_description}. Vibe is {vibe}. For Branding Purposes the orginal site is included as reference.` Each call costs ~$0.18 ($0.165 image + small gpt-5.5 wrapper). |
| Human mockup review | Compares original screenshot to generated mockup | Approve, revise, regenerate, or skip | Path to test | Internal write | This is the next operator step and is currently skipped. It should happen before any outreach. |
| Outreach drafter | Writes the message or letter copy | Draft outreach text | Static template only; AI drafting Path to test | Draft-only | Current copy is a static template that interpolates business name and city only. AI-drafted personalization not run yet; must avoid unsupported claims and spammy framing. |
| Letter renderer | Builds a printable 3-page packet (address, letter, mockup) on demand from a stored lead + its latest mockup artifact | `outreach.html` + `outreach.pdf` per lead, registered as `outreach_pdf` artifact | Verified for narrow local runs | Draft-only | Entry point is `render_outreach_for_lead(lead_id, run_id, store, output_dir)`. No longer called in-loop by the runner — rendering happens after the operator has reviewed the mockup. Implemented in `src/outreach_packet.py` + `src/outreach_template.html` using Playwright + pypdf. Mockup page size assumes 96 DPI, so taller AI mockup dimensions can produce pages larger than US Letter; print scaling is the operator's call. |
| PostGrid letter sender | Sends approved physical letters | Provider ID, proof/status/tracking | Path to test; cannot test now | External write | PostGrid supports letters from templates, one-off HTML, local PDFs, and public PDF URLs. First test should be test mode, then one live letter to the operator's own address. |
| Review UI (FastAPI operator shell at `ui/server.py`) | Lets operator inspect and approve packets | Human decisions and workflow state | Path to test | Internal write until sending | Must show source receipt, screenshot, review, mockup, letter proof, cost, and send controls. |

For any external outreach:

- The operator must approve the exact final artifact.
- The UI must show the business identity, source evidence, contact/address source, mockup, draft/proof, cost, and send destination.
- Keep one-by-one sending until quality, compliance, suppression behavior, and provider reliability are proven.
- Store provider IDs, timestamps, final content, recipient, cost, and status.

## Screenshot Capture Scale Rule

Start with local Playwright. Switch a run to Browserbase only when at least one threshold is met:

- more than 150 website screenshots in one ZIP run and the operator wants completion under 15 minutes
- more than 1,000 screenshots per month
- more than 300 screenshots in a day, or more than 3 ZIP runs in a day
- local runtime exceeds 60 minutes for one normal ZIP screenshot batch
- more than 10% of otherwise valid websites fail after one retry because of browser crashes, resource exhaustion, or automation instability
- local screenshot concurrency above 5 pages causes sustained CPU or memory pressure
- screenshots need to run unattended while the operator machine may be asleep/offline
- more than 5% of otherwise valid sites show access denied, CAPTCHA, or bot-blocking behavior

When Browserbase is used, batch multiple captures into each remote session when possible because short sessions can be inefficient.

## Workflow Map

1. Enter one ZIP code: operator input, deterministic code.
2. Geocode ZIP and create search plan: deterministic code, Google dependency.
3. Fetch businesses from Google Places: vendor/API dependency, read-only.
4. Dedupe, persist leads via `WorkflowStore.upsert_lead` + `link_lead_to_run`, register vendor receipt as a `source_receipt` artifact: deterministic code, internal write.
5. Resolve/verify website URLs: deterministic code with possible human review.
6. Capture site screenshots; record `screenshot` artifact: browser automation, internal write.
7. Run cheap multimodal filter; record `filter` step_attempt + provider_call: AI judgment, draft-only.
8. Run redesign review and mockup prompt generation on survivors; record `review` step_attempt + provider_call: AI judgment, draft-only.
9. Generate mockup image if `needs_redesign` is true; record `mockup` artifact: AI image generation, draft-only.
10. **Runner stops here.** Review original site vs mockup: human judgment, currently skipped but required before outreach.
11. Draft outreach copy: currently a static template (business name + city interpolated); future AI writing, draft-only.
12. Render printable letter proof on demand via `render_outreach_for_lead`; records `outreach_pdf` artifact: deterministic code, draft-only.
13. Review packet in the FastAPI operator UI (`ui/server.py`): human judgment, internal write.
14. Send approved physical letter through PostGrid: external write, not autonomous, not tested yet.

## AI Job

AI should handle work that static code cannot do well enough:

- screen out non-prospect, unsafe, and adversarial pages cheaply before paying for the expensive review
- judge whether a website is a good redesign opportunity from visual evidence
- explain that judgment in plain English
- infer a better visual direction from the business, screenshot, and target customer
- write a strong image-generation prompt for the mockup
- generate the mockup image
- draft personalized outreach that references real observations

AI should not own source access, dedupe, retry logic, cost control, compliance rules, suppression lists, final send approval, or deciding whether repeated outreach is safe.

## Static Code Job

Normal code and tool-backed workflow should handle:

- Google Places calls, field masks, pass budgets, and source receipts
- ZIP geocoding, viewport/radius/grid planning, and pass labels
- dedupe by Place ID, website/domain, phone, address distance, and business name
- filtering out businesses without websites
- screenshot capture, retries, timeouts, and failure reasons
- workflow state in SQLite (`data/state/agent.db`); disk artifacts (screenshots, mockups, PDFs, vendor receipts) under `data/runs/{run_id}/`
- cost tracking via `provider_calls.cost_usd`, summed per run by JOIN with `step_attempts`; PostGrid + Google Places cost still TODO
- status updates and audit logs
- direct-mail HTML/PDF rendering with approved images
- PostGrid test/live mode handling, idempotency keys, provider IDs, proofs, tracking, and send records
- suppression list and one-by-one blast-radius limits
- FastAPI operator UI state and review controls

## Control Model

Best current fit: workflow with bounded AI steps and human review.

This is not a full agent loop yet. The flow is linear enough that deterministic orchestration should own most of it. A bounded assistant can help the operator inspect leads, regenerate mockups, revise outreach, and explain packet quality, but autonomy stops before external outreach.

Multi-agent control is premature. The core tools are still being tested, and the first goal is one useful end-to-end proof for the builder.

## Manual Proof

Smallest proof:

1. Pick one dense ZIP code.
2. Run broad Google Places harvest.
3. Dedupe and filter to businesses with websites.
4. Manually inspect the candidate list for relevance, duplicates, chains, and outside-area spillover.
5. Capture screenshots for 10 to 20 websites.
6. Generate redesign reviews for 10 sites.
7. Generate mockups for the best 5 opportunities.
8. Manually compare each original screenshot to its mockup.
9. Draft outreach for the best packets.
10. Render one letter proof with the mockup/photo.
11. When PostGrid is available, send one test-mode letter, then one live letter to the operator's own address.
12. Only after that, consider one manually approved prospect send.

This proof should answer:

- Can Google Places produce enough relevant website-bearing leads from one ZIP?
- Does broad harvesting improve yield without too much duplicate noise?
- Do screenshot capture and review work reliably enough for batches?
- Are mockups good enough to make outreach stronger?
- Does the packet save enough operator time to continue building?
- Can the physical letter proof include the mockup cleanly and affordably?

## Open Gaps

- Cost tracking covers OpenAI filter + review + image_generation via `provider_calls.cost_usd`; Google Places calls and PostGrid sends are not yet recorded as provider_calls.
- Google Places broad-harvest recall is not proven.
- Dedupe quality is not proven beyond one small run.
- Screenshot batch reliability is not proven. Screenshot failures currently print to stdout but do not write a `step_attempts` row, so the DB cannot answer "which leads were attempted but failed at the screenshot stage?" — wrap `capture_screenshot` in `start_step`/`fail_step` to close this.
- Review calibration is not proven against human judgment.
- Mockup quality has only been checked on one business.
- The DB is queryable, but no operator-friendly run browser exists yet. Ad-hoc inspection is via `sqlite3 data/state/agent.db`.
- The human original-vs-mockup review UI is not built. The `outreach.pdf` per lead can be opened by the operator as a stopgap reviewable artifact, but rendering is now opt-in (the runner stops after the mockup).
- Outreach letter copy is a fixed template; AI-drafted personalization that references real screenshot observations is not built.
- Mockup page in `outreach.pdf` is sized at 96 DPI, which means typical AI image dimensions can produce pages larger than US Letter (e.g. 10.67×16in for a 1024×1536 mockup). Print scaling and final mailing form factor are not yet decided.
- `run_id` is regenerated each invocation (`{slug}_{timestamp}`), so re-running the same ZIP creates a new run rather than resuming. Idempotency machinery is in place but not exposed via a `--resume` flag yet.
- PostGrid cannot be tested right now.
- Physical-mail compliance, suppression, and cost controls still need design.

## Not Now

- Fully autonomous outreach.
- Bulk sending.
- Multi-agent lead generation.
- Selling this as a SaaS to agencies.
- Purchased lead/email lists.
- D&B/Data Axle or other paid business databases before Google Places recall is tested.
- Browserbase before local Playwright crosses the scale thresholds.
