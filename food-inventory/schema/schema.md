# Food Inventory — Data Schema v0.1

Designed for Glide tables (Phase 1 of the 6-phase roadmap in the wiki), but
deliberately app-agnostic so the same shapes work if the backend ever moves.
Every row is tagged `family_id` from day one, per Lindsey's multi-household
setup decision.

## Entities

### Item (the core table — current inventory)

One row per thing-in-the-house. This is what the app shows and what "what's
low / what's expiring" queries run against.

| Field | Type | Notes |
|---|---|---|
| `item_id` | string | Unique ID (Glide row ID works fine) |
| `family_id` | string | Household tag — required on every row |
| `name` | string | Normalized display name ("Barilla Rotini") |
| `brand` | string | Optional ("Barilla", "H-E-B", "Reggano") |
| `category` | string | FK → Category (`pantry`, `snacks`, `produce`, …) |
| `location` | string | FK → Location (`pantry-cabinet`, `fridge`, …) |
| `quantity` | number | Count or approximate amount |
| `unit` | string | `count`, `box`, `bag`, `jar`, `can`, `lb`, `oz`, `pct` (pct = "about 50% left") |
| `status` | enum | `plenty` / `low` / `out` — the field the shopping list is built from |
| `expiration` | date | From package if visible, else estimated from shelf-life default |
| `expiration_source` | enum | `label` / `estimated` / `unknown` |
| `added_via` | enum | `receipt_scan` / `photo_scan` / `voice` / `manual` |
| `confidence` | enum | `high` / `medium` / `low` — how sure the scanner was; low-confidence rows get surfaced for user review instead of silently trusted |
| `source_ref` | string | Which scan/dump created it (e.g. `receipt-2026-06-heb`, `photo-2026-07-12-pantry`) |
| `notes` | string | Free text ("clipped bag, half left") |
| `updated_at` | datetime | Last touch |

### PurchaseEvent (receipt history)

One row per receipt line item. Feeds price history, budgeting (Phase 6), and
auto-restock of the Item table ("you bought yogurt again → reset status to
plenty").

| Field | Type | Notes |
|---|---|---|
| `purchase_id` | string | Unique ID |
| `family_id` | string | Household tag |
| `receipt_id` | string | Groups line items from one receipt |
| `store` | string | "H-E-B", "Costco", "Kroger" |
| `purchase_date` | date | From receipt when printed, else user-supplied |
| `raw_text` | string | The literal receipt line ("HEB NAT BNLS SKNLS CHKN B") — keep for parser debugging |
| `name` | string | Normalized name ("H-E-B Natural Boneless Skinless Chicken Breast") |
| `quantity` | number | |
| `unit_price` | number | |
| `line_total` | number | |
| `category` | string | FK → Category |
| `is_inventory` | boolean | False for donations, bag fees, gift cards — excluded from the Item table |

### Location

Small lookup table the user can edit. Seeded: pantry cabinet, snack cabinet,
canned-goods cabinet, fridge, freezer, bathroom (toiletries later).

### Category

Lookup table with a `default_shelf_life_days` column — this is how estimated
expirations get computed when the package date isn't visible. Seeded values
are deliberately rough; tighten them as real data comes in.

## How the three input paths converge

```
receipt photo ─┐
shelf photo  ──┼──► vision/LLM parse ──► Item rows (+ PurchaseEvent rows for receipts)
voice dump   ──┘        (same JSON schema for all three)
manual entry ─────────► Item row directly (Glide form)
```

The whole design bet: **every input method produces the same `Item` shape**,
so the app never cares how a row got there. `added_via` + `confidence` +
`source_ref` preserve the provenance for review flows.

## Voice dictation flow (the brain-dump path)

1. User taps a "Brain dump" button → a plain text field with the phone's
   native keyboard dictation (no audio infra needed for v1 — the OS does
   speech-to-text for free).
2. User rambles: *"okay we've got like three boxes of pasta, a jar of peanut
   butter that's almost gone, two cans of enchilada sauce, we're out of
   eggs..."*
3. The transcript goes to the same parser as photos (`intake.py voice`),
   which returns Item rows — including `status: out` for "we're out of eggs"
   and `status: low` for "almost gone".
4. Rows land in a review list; user confirms/edits, then they merge into
   inventory.

v2 upgrade path: record audio → server-side transcription → same parser.
Nothing downstream changes, which is the point.

## Dedup/merge rule (keep it simple for v1)

On insert, match on `(family_id, lower(name))`. If a match exists:
- receipt scan → bump quantity, set `status: plenty`, add PurchaseEvent
- photo/voice → update quantity/status in place
- else insert new row

Fuzzy matching ("PB" vs "peanut butter") is a later problem — the parser
already normalizes names, which gets you most of the way.
