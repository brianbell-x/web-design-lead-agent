# UI Tests

Load the UI and attempt to complete each task below. Note any problem you encounter, any step that fails or is ambiguous, and any room for improvement.

URL: `http://127.0.0.1:8765/` — launch with `python -m ui.server` from `web-design-lead-gen/`.

These flows cover the path a demo video walks through: empty state, kicking off a run, watching live activity produce leads, reviewing the approval workbench, zooming into each exhibit, navigating the queue, and approving a lead.

Start with an empty queue (`data/state/agent.db` has no approval-ready leads). Flow 1 begins on that empty page, starts a run, and produces the leads the later flows depend on.

## Flow 1 — Land on the empty state and start a run

1. Open `http://127.0.0.1:8765/`
2. Read the "Nothing pending." heading
3. Read the reason line under the heading
4. Read the pip color and label in the top-left of the masthead
5. Read the ticker button label "▢ open log" in the top-right
6. Click the location input
7. Type "78704" into the location field
8. Click "start run"
9. Wait for the "Scanning 78704." heading to replace "Nothing pending."
10. Watch the pip and label in the masthead change to the running state
11. Read the elapsed timer that appears in the masthead after step 9 (initially 00:00:00)
12. Watch the ticker button for new events
13. Watch the elapsed timer increment
14. Leave the page open until the approval workbench appears with at least one lead

## Flow 2 — Review the approval workbench layout

1. Open `http://127.0.0.1:8765/` with at least one approval-ready lead in queue
2. Read the live/idle/error pip in the top-left of the masthead
3. Read the ticker line in the masthead
4. Read the queue count in the top-right of the masthead
5. Read the "Exhibit A · current site" label
6. Read the business name, sector, and locale under Exhibit A
7. Read the "Exhibit B · proposed redesign" label
8. Read the caption under Exhibit B showing business name and lead position
9. Read the "Exhibit C · the letter" label
10. Read every paragraph of the letter in Exhibit C
11. Read the business name and address under Exhibit C

## Flow 3 — Open and close the current-site modal

1. Open `http://127.0.0.1:8765/` with at least one approval-ready lead
2. Click the Exhibit A frame
3. Look at the full-size screenshot
4. Read the "Exhibit A — current site" title
5. Press the Escape key

## Flow 4 — Open and close the mockup modal

1. Open `http://127.0.0.1:8765/` with at least one approval-ready lead
2. Click the Exhibit B frame
3. Look at the full-size AI mockup
4. Press the Escape key

## Flow 5 — Open and close the letter modal

1. Open `http://127.0.0.1:8765/` with at least one approval-ready lead
2. Click the Exhibit C frame
3. Read every line of the enlarged letter
4. Press the Escape key

## Flow 6 — Navigate forward through the queue

1. Open `http://127.0.0.1:8765/` with at least two approval-ready leads
2. Read the "Lead X / Y" caption under Exhibit B
3. Click "Next"
4. Read the new "Lead X / Y" caption under Exhibit B

## Flow 7 — Approve a lead

1. Open `http://127.0.0.1:8765/` with at least one approval-ready lead
2. Click "Approve & mail"
3. Watch the button label change to "Rendering PDF…"
4. Wait for the next lead to load or for the empty state to appear
