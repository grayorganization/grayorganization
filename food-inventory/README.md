# Food Inventory — The Bones

Full data model + working pipeline for the food & toiletries inventory app
(see `wiki/projects/food-inventory` in the obsidian-wiki). Platform-agnostic:
SQLite now, Postgres/Supabase when there's an app in front of it, and any UI
layer (native, web, or no-code) can sit on top.

## What's here

| Path | What it is |
|---|---|
| `schema/schema.sql` | **The canonical model** — 6 tables + 4 views, SQLite dialect, ports to Postgres unchanged |
| `schema/schema.md` | The design doc: why products/items are split, field notes, portability |
| `data/*.csv` | Seed data parsed from 4 cabinet photos + 1 H-E-B receipt (2026-07-12): 50 items, 13 receipt lines, lookups |
| `scripts/build_db.py` | Seed CSVs + schema → `inventory.db` (idempotent, rebuild anytime) |
| `prototypes/intake.py` | The parser: receipt / shelf photo / voice brain-dump → upserted into the db |
| `app/` | **The web app** — mobile-first FastAPI UI over the db (see below) |

## Quick start

```bash
cd food-inventory
pip install -r requirements.txt
python3 scripts/build_db.py          # → inventory.db with the real kitchen seeded
uvicorn app.main:app                 # → http://localhost:8000

# CLI intake also works standalone (needs ANTHROPIC_API_KEY):
python3 prototypes/intake.py receipt receipt.jpg --db inventory.db
python3 prototypes/intake.py voice "peanut butter's almost gone, we're out of eggs" --db inventory.db
```

## The app

Four screens, mobile-first, zero JS dependencies (server-rendered forms —
nothing to break):

- **Inventory** — everything on hand, grouped by location, searchable, with a
  tap-to-set `plenty/low/out` segmented control per item and a manual-add form.
- **Shopping** — generated live from status (`low`/`out`), one-tap "Bought"
  restocks, plus the use-it-up list (expiring ≤ 7 days).
- **Capture** — the brain-dump textarea (phone keyboard dictation) and a
  photo upload (receipt or shelf, camera-capture enabled on mobile). Both go
  through the same parser/upsert as the CLI. Needs `ANTHROPIC_API_KEY`;
  everything else works without it.
- **Review** — scanner output below high confidence waits here for a
  "looks right" / "remove" decision. Badge counts in the tab bar.

To use it from your phone on the same wifi:
`uvicorn app.main:app --host 0.0.0.0` then open `http://<computer-ip>:8000`.

## The model in one paragraph

**Products** are kinds of things ("Barilla Rotini" — what barcodes, prices,
and recipes key against); **items** are stock on hand (that product, in a
location, with a quantity and a `plenty/low/out` status). **Receipts +
receipt_lines** are purchase history linked back to products. **Households**
tag every row so multi-family is a WHERE clause, not a rebuild. Four views do
the actual job: `shopping_list` (status ≠ plenty), `expiring_soon`,
`price_history`, and `review_queue` (scanner guesses awaiting a human).
Full rationale in `schema/schema.md`.

## Input paths — all converging on one upsert

1. **Manual** — app form, writes products/items directly.
2. **Receipt scan** — vision model reads the photo in one call (no OCR step),
   expands abbreviations, records the receipt + lines, restocks items.
3. **Shelf/fridge photo** — itemizes a cabinet; how the seed data was made.
4. **Voice brain-dump** — phone-native dictation → transcript → same parser.
   "Almost gone" → `low`, "we're out of" → `out`; the shopping list updates
   in real time as you talk. No audio infrastructure in v1 — the OS keyboard
   does speech-to-text.

Merge rules live in one place (`intake.py:upsert_item`): product matched by
case-insensitive name per household, item matched by product + location,
update-in-place otherwise insert. Verified working end-to-end against the
seeded db.

## Status / known gaps

- Seed rows with `confidence: low|medium` sit in `review_queue` (18 rows)
  until confirmed — that's by design.
- Product matching is exact-name (post-LLM-normalization); fuzzy matching
  deferred.
- Barcode + label-date scanning (Phase 2) not started; `products.barcode`
  is already there for it.
- Recipe matching (Phase 4) needs only new recipe tables — `items` is ready.
- No app layer yet. Next candidates: a tiny FastAPI + HTMX front end over
  the SQLite file, or Supabase + a mobile shell — schema works for either.
