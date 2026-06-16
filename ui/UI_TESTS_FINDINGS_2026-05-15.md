# UI_TESTS findings — 2026-05-15

Ran flows that did not require approval-ready leads (1, 2, 3, 4, 21). DB had 0 `lead_decisions`, so flows 5–20 were skipped per user direction. Findings cross-reference `ui/UI_TESTS.md`.

## Bugs (UI ↔ code reality)

### B1. "previous run died unexpectedly" banner is unreachable through the natural recovery path (Flow 21 step 5)

`ui/server.py:234` calls `s.recover_dead_runs()` inside `header_ctx` **before** `_workflow_state` (`ui/server.py:185`) checks `runs.status='running'`. `recover_dead_runs` (`src/core.py:247`) flips dead-pid runs to `'abandoned'`, so by the time `_workflow_state` runs, the latest row is `'abandoned'` and the function returns `'idle'`. The `workflow_state == 'error'` branch in `ui/templates/_runform.html:1` — which renders `previous run died unexpectedly — start a new one` — can therefore never fire on natural reload after a crash. Observed: killed the runner mid-scan, reloaded, got the plain empty state with no banner.

### B2. Race window between `runs` INSERT and first `step_attempts` INSERT renders a fresh run as "Error" with the "previous run died" banner (Flow 1, Flow 2)

`_workflow_state` returns `'error'` when `runs.status='running'` AND no `step_attempts` for that `run_id` are still `'running'`. After POST /runs, the `runs` row is inserted immediately but the subprocess takes several seconds to import Playwright and write its first `step_attempts` row. During that gap (~3–6 s observed) the UI shows pip "Error" + the "previous run died unexpectedly" banner for a brand-new run. Reproduced on both `78704` and `Austin, TX`.

### B3. POST /runs allows a second runner subprocess to spawn while a "running" row is still in the DB

Because `_workflow_state` returns `'error'` (not `'running'`) during B2's race window, the guard at `ui/server.py:460` (`if _workflow_state(s._conn) == "running"`) does not block a second submit, and a second `src.runner` is spawned. Confirmed by `Get-CimInstance Win32_Process` showing two concurrent `python -m src.runner --zip 78704` processes after submitting twice in quick succession. The check should be `!= 'idle'` (or explicitly include `'error'`).

### B4. Stale `step_attempts.status='running'` rows leak across run termination

Run `90210_20260516T011233Z` has terminal status `no_needs_redesign_lead_found` but three `step_attempts` for it are still `status='running'`. `recover_dead_runs` (`src/core.py:260`) only resets step_attempts whose parent run is currently `'running'`, so when a run reaches a normal terminal status with steps still in `'running'` (e.g. mockup step hung), those rows leak forever. No UI symptom today because `_workflow_state` filters on the latest run, but it would corrupt any cross-run "active work" check.

### B5. Server-side ZIP regex accepts any 5 digits; non-US codes start a real subprocess

`ui/server.py:450` only requires `\d{5}`. Submitting `99999` (not a real US ZIP) bypasses the form, spawns a runner that immediately dies, and leaves an `abandoned` row + a misleading runner.log. Validate against `data/geonames/US.txt` (already loaded for ZIP lookup) and return `?error=invalid_input` for unmatched 5-digit codes.

## UI_TESTS.md issues (test ↔ UI mismatches)

### T1. Flow 1 step 5 — "Live Activity" panel does not exist

Top-right region is a `<button class="ticker">` with `title="Open pipeline log"` and the hint label "▢ open log". There is no header text "Live Activity". Rename in tests or add the label.

### T2. Flow 1 step 6 — no elapsed timer in the masthead on the empty state

`elapsed` only appears in the masthead **after** a run starts (`<div class="elapsed">00:00:05</div>` shows up alongside the "Live" pip). On the empty state the only digit-clock element is the latest event's wall-clock timestamp inside the ticker (`<span class="ts">20:21:34</span>`). Reword step 6 to "after step 9 (start run)" or move it later.

### T3. Flow 3 step 4 — "red error banner" never appears for `not-a-zip`

The kickoff input has HTML5 `pattern="(\d{5})|([A-Za-z .'\-]+,\s*[A-Za-z]{2})"`. Submitting `not-a-zip` is rejected client-side with a native browser tooltip ("Please match the requested format."); the form never POSTs, so the server-side red banner `expected 5-digit zip or 'city, st' (got '…')` is unreachable. Either drop the HTML5 `pattern` (let the server own validation and render the banner) or update the test to describe the native tooltip.

### T4. Flow 4 steps 3–4 — "Type … into the location field. Click 'start run'" is impossible while a run is running

`_runform.html` only renders the form when `workflow_state == 'idle'` (or `'error'`). When a run is genuinely `'running'`, the new tab shows the scanning state with **no form**. Without B2/B3 the user cannot reach step 3 at all. Rewrite Flow 4 to "Open `/?error=run_in_progress` or POST to `/runs` while a run is active" — or, better, fix the UI to show the form-and-banner pattern even during a run so the test step is reachable.

### T5. Flow 1 / Flow 2 — heading punctuation differs from the page title

`<h1>` says `Scanning 78704.` (period). `<title>` says `scanning 78704…` (lowercase + ellipsis). Pick one.

### T6. Flow 21 step 2 — `src/runner.py` PID in `runs.runner_pid` is sometimes stale

On Windows, `python -m src.runner` produces a parent launcher + the actual worker. The pid recorded in `runs.runner_pid` (`src/core.py:244` uses `os.getpid()`) is the **server's** PID, not the runner subprocess's. `recover_dead_runs` then checks the wrong pid. (Side-effect: in this session it happened to work because the server outlives any run, but the function never actually detects a runner crash — only a server crash.)

### T7. File path in the doc header is wrong

`UI_TESTS.md:5` says "launch with `python -m ui.server` from `web-design-lead-agent/`". The directory is `web-design-lead-gen/`. `pyproject.toml` does pin `name = "web-design-lead-agent"` so the confusion is real, but the launch path is the dir name.

## Verified working

- Flow 1 steps 1–13: empty heading, reason line, pip Idle (rgb 107,95,79), kickoff submit, pip → Live, scanning heading, elapsed timer increments, ticker streams events (`KEPT TIKI TATSU-YA`, `NO REDESIGN NEEDED FOR ODD DUCK`, etc.).
- Flow 2: city/state regex `Austin, TX` is accepted; runner subprocess is spawned with `--city Austin --state TX`.
- Flow 3 follow-up (step 5–6): once the value is corrected to `78704`, the form submits and the kickoff flow proceeds.
- Server-side error banner content: navigating to `/?error=invalid_input&value=not-a-zip` does render `expected 5-digit zip or 'city, st' (got 'not-a-zip')` in the warm-orange `.err` block.
- Form preserves `submitted_value` after server-side validation failure (good for editing).

## Suggested order of fixes

1. **B1 + B2** together — split the dead-run state out of `runs.status` (e.g. `'crashed'`) instead of overloading `'abandoned'`, so the "previous run died" banner can survive `recover_dead_runs`; also gate the `'error'` branch behind "step_attempts have been written before but none are running now," not "no step_attempts at all."
2. **B3** — make the POST /runs guard `_workflow_state != 'idle'`.
3. **T6 + B4** — fix `runs.runner_pid` to record the subprocess pid (return it from `asyncio.create_subprocess_exec` and `UPDATE runs SET runner_pid=?`), and have `recover_dead_runs` also reset step_attempts on terminal runs.
4. **T1–T5, T7** — text/doc patches in `UI_TESTS.md` and templates.
