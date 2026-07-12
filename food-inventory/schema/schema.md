# Food Inventory — Data Model v0.2

Platform-agnostic. The canonical definition is `schema.sql` (SQLite dialect,
ports to Postgres/Supabase without structural change); this doc explains the
shape and the decisions. Nothing here assumes Glide or any particular app
layer — a native app, a web app, or a no-code tool can all sit on top.

## The shape

```
households ──< locations
    │
    ├──< products ──────< items            (stock on hand)
    │       │
    │       └──────< receipt_lines >── receipts   (purchase history)
    │
categories ──< products (global lookup, carries default shelf life)
```

Six tables. The one structural decision that matters:

**Products vs Items.** A *product* is a kind of thing ("Barilla Rotini") — the
catalog row that barcodes, price history, and recipe matching key against. An
*item* is stock on hand — that product, in a location, with a quantity and a
status. Splitting them means:

- Buying rotini again doesn't create a duplicate — it bumps the existing
  item and adds a receipt_line pointing at the same product.
- Price history falls out of `receipt_lines` joined to `products` for free.
- Barcode scanning (Phase 2) is just filling in `products.barcode`.
- Dedup is a database constraint, not app logic: unique index on
  `(household_id, name COLLATE NOCASE)`.

Everything else is deliberately flat. No separate units table, no inventory
event log, no user accounts yet — those can be added without reshaping what
exists.

## Field notes

- **`items.status`** (`plenty`/`low`/`out`) is the single field the shopping
  list is generated from. Voice input maps "almost gone" → `low` and "we're
  out of" → `out` directly onto it.
- **`items.confidence` + `items.reviewed`** — scanner output isn't silently
  trusted. Anything below `high` confidence sits in the `review_queue` view
  until a human confirms it.
- **`items.added_via` + `items.source_ref`** — full provenance for every row
  (`receipt_scan` / `photo_scan` / `voice` / `manual`, plus which scan).
- **`categories.default_shelf_life_days`** — how estimated expirations get
  computed when no label date is visible.
- **`receipt_lines.raw_text`** keeps the literal receipt line ("HEB NAT BNLS
  SKNLS CHKN B") next to the normalized name, so parser mistakes are
  debuggable forever.
- **`household_id` on every domain table** — multi-household is a WHERE
  clause (or a Postgres RLS policy later), not a rebuild.

## The views (the point of the whole app)

Defined in `schema.sql`, these are the three queries that solve the
grocery/meal-planning problem:

| View | Answers |
|---|---|
| `shopping_list` | "What do I need to buy?" — items with status `low`/`out` |
| `expiring_soon` | "What should I use up this week?" — expirations within 7 days |
| `price_history` | "What do I usually pay for this?" — receipt lines per product |
| `review_queue` | "What did the scanner guess at?" — unreviewed low/medium-confidence rows |

"What can I make with what's on hand" (Phase 4) is recipe data joined against
`items` — the model already supports it; only the recipe tables are new.

## Input convergence

```
receipt photo ─┐
shelf photo  ──┼──► vision/LLM parse ──► upsert products + items
voice dump   ──┘        (one parser,      (+ receipts/receipt_lines
manual entry ──────► app form ──────┘       for receipt mode)
```

All four paths converge on the same upsert (`prototypes/intake.py:upsert_item`):

1. Find product by `(household, lower(name))` — create if missing.
2. Find item by `(product, location)` — update quantity/status in place if it
   exists, else insert.
3. Receipt mode also writes the receipt + lines, linked to products by name.

Fuzzy product matching ("PB" vs "peanut butter") is deferred; the LLM parser
normalizes names before they hit the database, which covers most of it.

## Portability

- **SQLite now**: zero infrastructure, single file, works offline, good
  enough for years of household data.
- **Postgres/Supabase later**: swap `TEXT` datetime defaults for
  `timestamptz`, keep everything else. `household_id` columns become RLS
  policies. Views port as-is.
- **If a no-code layer (Glide etc.) ever returns**: each table maps to one
  Glide table; the views become computed columns/filters.
