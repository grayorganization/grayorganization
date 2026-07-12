# Food Inventory — Starting Bones

Bootstrap package for the food + toiletries inventory app (co-led with
Lindsey; see `wiki/projects/food-inventory` in the obsidian-wiki). This is
Phase 1 material: the data model, a real seed inventory extracted from photos
of the actual kitchen, and a working parser prototype for the scanning lane.

## What's here

| Path | What it is |
|---|---|
| `schema/schema.md` | Data model: Item, PurchaseEvent, Location, Category — designed for Glide tables, multi-household from day one |
| `data/items.csv` | **50 seed inventory rows** parsed from 4 cabinet photos + 1 H-E-B receipt (2026-07-12) — importable straight into a Glide table |
| `data/purchases.csv` | The H-E-B receipt as PurchaseEvent rows (13 lines, $132.00 total) |
| `data/locations.csv` | Location lookup seeded from the actual kitchen |
| `data/categories.csv` | Category lookup with rough default shelf-life values (drives estimated expirations) |
| `prototypes/intake.py` | The scanning/parsing prototype — receipt, shelf photo, and voice dictation all through one parser |

## The core design decision

**Every input path produces the same Item shape.** Receipt scan, cabinet
photo, voice brain-dump, and manual entry all converge on one row format with
`added_via` / `confidence` / `source_ref` fields preserving provenance. The
app displays one inventory list and never cares how a row got there — and
low-confidence rows go to a review queue instead of being silently trusted.

## Input paths

1. **Manual entry** — a Glide form writing directly to the Items table. No
   code needed; Lindsey's lane.
2. **Receipt scan** — `python intake.py receipt photo.jpg`. Vision model reads
   the receipt directly (no separate OCR step — the earlier Tesseract→LLM plan
   collapses to one call), expands store abbreviations, splits multi-quantity
   lines, flags non-inventory lines like donations.
3. **Shelf/fridge photo** — `python intake.py shelf photo.jpg --location fridge`.
   Full auto-detection is Phase 3 in the roadmap, but as a manual "audit my
   cabinet" action it works today and seeded most of `items.csv`.
4. **Voice brain-dump** — `python intake.py voice "three boxes of pasta, peanut
   butter's almost gone, we're out of eggs"`. See below.

## Voice dictation (the low-friction path)

The v1 trick: **don't build audio infrastructure.** Glide gives you a text
field; the phone's native keyboard dictation turns speech into text for free.
So the flow is: Brain-dump button → user talks into the mic key → transcript
hits the same parser as photos → items land in a review list with statuses
already mapped ("almost gone" → `low`, "we're out of" → `out`, which is
exactly what the shopping list is generated from).

v2 can swap in recorded audio + server transcription without changing
anything downstream.

## Running the prototype

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...

python prototypes/intake.py receipt receipt.jpg
python prototypes/intake.py shelf pantry.jpg --location pantry-cabinet
python prototypes/intake.py voice "two cans of enchilada sauce, out of eggs"

# Append parsed rows to the seed CSV for Glide import:
python prototypes/intake.py shelf fridge.jpg --location fridge --csv data/items.csv
```

Wiring into Glide later: wrap `intake.py` behind a small HTTP endpoint
(Cloud Run / Lambda), call it from a Glide workflow with the photo URL or
transcript, write the returned rows into the Items table.

## Why this solves the actual problem

Grocery shopping and meal planning fail when you don't trust the list. With
`status` + `expiration` populated, three views fall out for free:

- **Shopping list** = items where `status != plenty`
- **Use it up** = items expiring in the next N days
- **What can I make** = recipe matching against on-hand items (Phase 4 — the
  data model already supports it)

## Known gaps (deliberately)

- Dedup is naive name-matching (documented in schema.md) — fine for v1.
- Seed rows marked `confidence: low` need a quick human pass (7 of 50).
- Barcode + expiration-date scanning (Phase 2) not started.
- This repo isn't the app repo — when ready, this folder's contents move to
  `gray-labs-app/food-inventory`.
