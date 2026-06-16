# Flat Mockup To Code Findings

## Input

Recursive search found two generated mockups in `data/runs/38107_20260516T140400Z/`:

- `leads/smoky-city-bbq__chijivexsk/mockup.png`
- `leads/iskiwitz-metal-s-scrap-metal-recycling__chijl6i1ha/mockup.png`

This proof rebuilds the Smoky City BBQ mockup because it is the richer test case: large display typography, photo collage, torn paper panels, navigation, CTA buttons, side menu, and bottom content bands.

## Mockup Contents

The mockup is a 1536x1024 grungy barbecue homepage:

- Header with Smoky City BBQ logo, nav links, order button, and bag icon.
- Hero headline: `MEMPHIS MADE SINCE '08`.
- Left rail copy: `BBQ. FISH. CHICKEN. LOCAL. ALWAYS.` plus a `901` Memphis stamp.
- Large barbecue plate photo with torn paper note: `SLOW SMOKED. BIG FLAVOR. DOWN HOME.`
- Red right-side menu panel: `Pick Your Perfect Plate` with plate categories.
- Bottom collage: storefront photo, `South Memphis Soul.`, `Dine In`, `Order Online`, Memphis skyline, and social row.

## Rebuilt Proof

Created `proofs/flat-mockup-to-code/index.html` as a dependency-free offline page with local assets in `assets/`:

- `food.jpg`
- `storefront.jpg`
- `skyline.jpg`

The proof recreates the layout as a fixed 1280x853 artboard for screenshot comparison. Text, panels, buttons, torn-paper shapes, color palette, and most composition are HTML/CSS. Photo-heavy regions are cropped from the flat mockup into local assets.

## Verification

`proofs/flat-mockup-to-code/render.py` screenshots the page at 1280px wide and writes:

- `proofs/flat-mockup-to-code/render.png`

Command used:

```powershell
.\.venv\Scripts\python.exe proofs/flat-mockup-to-code/render.py
```

Result: Playwright launched successfully from the project venv and generated `render.png`.

## Fidelity Gaps

- The logo is approximate live HTML/CSS, not the exact illustrated crossed-utensil mark.
- Fonts use local system fallbacks (`Impact`, Arial Narrow style families), so the condensed distressed type is close but not exact.
- The grunge/distress texture is simulated with CSS noise and gradients, not extracted as precise texture layers.
- Food, storefront, and skyline are raster crops from the mockup. This preserves visual fidelity but is not fully editable.
- Small icon artwork in the menu and social row is simplified.
- Torn paper edges are CSS polygons, so they match the design language but not every exact tear contour.

## Judgment

PASS for productizing the flat-mockup-to-code path as the primary workflow. This proof shows the page can be rebuilt from one flat mockup without a decomposition step and can render offline as a working single-page site with recognizable same-design fidelity.

Do not expect pixel-perfect output from one blind generation pass. Productizing needs an automated visual refinement loop.

## Automation Requirements

- Input: mockup PNG, target viewport, business name, optional source screenshot, and desired asset policy (`crop photos`, `regenerate photos`, or `CSS/SVG only`).
- Prompt: require section-by-section reconstruction, exact visible copy, offline-only assets, no CDNs, and explicit reporting of raster-cropped versus live-editable elements.
- Asset step: detect photo regions and crop them locally; recreate logos/icons in SVG/CSS unless the crop is materially better.
- Validation: render generated HTML at the target width, compare against the source mockup using screenshot diff plus OCR/text checks, then iterate on layout, font scale, colors, and missing copy.
- Acceptance: same section order, matching palette, readable matching copy, no external network requests, and a business-owner side-by-side comparison that reads as the same design.

## Refinement Loop Proof

### Initial Comparison Deltas

The five most impactful deltas from comparing `render.png` against the source mockup were:

- The live CSS logo mark was too approximate: it missed the arced Smoky City lettering, crossed utensils, and ribbon structure, and the header bag icon rendered as text.
- The right note/menu stack had a layering error: `DOWN HOME.` was partially covered by the red panel, and the menu panel started too low.
- The menu heading wrapped as three lines instead of the mockup's two-line `Pick Your / Perfect Plate` composition, pushing all plate rows down.
- The bottom collage was too shallow and low: storefront, story, dine-in, order, skyline, and social blocks did not match the mockup's taller torn-paper band.
- The red `08` sat too small above the lower torn edge, weakening the interlock between `SINCE'`, the food photo, and the bottom collage.

All measurements below compare the rendered screenshot against the source mockup resized to the 1280x853 render viewport. Metric is mean absolute RGB error; lower is better.

| Pass | Overall | Header | Hero | Note/Menu | Bottom |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 53.83 | 34.34 | 63.26 | 55.79 | 46.55 |
| Round 1 | 53.29 | 32.97 | 63.39 | 53.31 | 43.96 |
| Round 2 | 53.16 | 32.97 | 63.46 | 53.49 | 43.39 |

### Round 1

Changed:

- Cropped the source logo into `assets/logo.png` and replaced the approximate live logo.
- Rebuilt the cart icon with CSS instead of text.
- Kept `ORDER ONLINE` on one line.
- Raised and deepened the red menu panel, lifted the note above it, and enlarged the red `08`.
- Increased the bottom collage height and raised the bottom panels to match the mockup's taller band.

Result:

- Measurably improved overall fidelity: overall MAE `53.83 -> 53.29`.
- Header, note/menu, and bottom regions improved.
- Hero region regressed slightly, `63.26 -> 63.39`, because the larger `08` matched the visual intent better but increased pixel mismatch around the food/photo overlap.

### Round 2

Changed:

- Forced the menu heading into the mockup's two-line shape and pulled the menu rows upward.
- Tightened the story panel heading/body scale so `SOUTH MEMPHIS SOUL.` remains two lines and the `OUR STORY ->` link becomes visible.
- Tested widening the note to the right edge, but rejected that sub-change after it worsened the note/menu metric.

Result:

- Improved overall again: `53.29 -> 53.16`.
- Bottom improved again: `43.96 -> 43.39`.
- Note/menu stayed better than baseline, `55.79 -> 53.49`, but was slightly worse than round 1 because the larger two-line menu heading changes the red panel pixel mass.
- Round 2 did converge on the aggregate metric after rejecting the note-width candidate, but the per-region result shows a production loop needs region-aware acceptance rather than a single global score.

### Automation Contract

Inputs per iteration:

- Source mockup PNG.
- Current HTML/CSS/assets bundle.
- Target viewport and device scale.
- Previous render, previous metrics, and optional rejected candidate notes.
- Region map for stable areas: header, hero, food/photo, note/menu, and bottom collage.

Pipeline pass to each iteration:

- A visual delta list ranked by region impact.
- Numeric diff by region, OCR/text checks for visible copy, and layout facts such as bounding boxes for large text, notes, menus, and bottom panels.
- The current asset policy: keep offline, allow local crops for brand/photo regions, no network assets.

Stop criteria:

- Required text is present and legible.
- No region regresses beyond a small threshold unless an explicit semantic check improves, such as fixing a wrong line break.
- Overall MAE improves or remains effectively flat while a high-priority semantic/layout delta is fixed.
- The loop stops after two non-improving accepted iterations, after all ranked deltas are below threshold, or when human review marks remaining deltas as acceptable art-direction differences.
