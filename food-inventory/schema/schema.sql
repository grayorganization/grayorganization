-- Food Inventory — canonical schema v0.2 (platform-agnostic)
-- SQLite dialect; designed to port to Postgres/Supabase without structural change.
-- Run: sqlite3 inventory.db < schema.sql   (or use scripts/build_db.py)

PRAGMA foreign_keys = ON;

-- Households: multi-tenancy from day one. Every domain row hangs off one.
CREATE TABLE households (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Categories are global (shared vocabulary across households).
-- default_shelf_life_days drives estimated expirations when no label date exists.
CREATE TABLE categories (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    default_shelf_life_days INTEGER
);

CREATE TABLE locations (
    id            TEXT PRIMARY KEY,
    household_id  TEXT NOT NULL REFERENCES households(id),
    name          TEXT NOT NULL,
    description   TEXT
);

-- Products: the catalog — one row per *kind* of thing ("Barilla Rotini"),
-- regardless of how many are on the shelf. This is what barcodes, price
-- history, and recipe matching key against.
CREATE TABLE products (
    id            TEXT PRIMARY KEY,
    household_id  TEXT NOT NULL REFERENCES households(id),
    name          TEXT NOT NULL,
    brand         TEXT,
    category_id   TEXT REFERENCES categories(id),
    default_unit  TEXT NOT NULL DEFAULT 'count',
    barcode       TEXT,                -- Phase 2: barcode scanning fills this in
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- The dedup rule lives here: one product per (household, name), case-insensitive.
CREATE UNIQUE INDEX idx_products_household_name
    ON products(household_id, name COLLATE NOCASE);
CREATE INDEX idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL;

-- Items: stock on hand — one row per product per location.
-- status is the field the shopping list is generated from.
CREATE TABLE items (
    id                 TEXT PRIMARY KEY,
    household_id       TEXT NOT NULL REFERENCES households(id),
    product_id         TEXT NOT NULL REFERENCES products(id),
    location_id        TEXT REFERENCES locations(id),
    quantity           REAL NOT NULL DEFAULT 1,
    unit               TEXT NOT NULL DEFAULT 'count',
    status             TEXT NOT NULL DEFAULT 'plenty'
                       CHECK (status IN ('plenty', 'low', 'out')),
    expiration         TEXT,           -- ISO date or YYYY-MM
    expiration_source  TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (expiration_source IN ('label', 'estimated', 'unknown')),
    added_via          TEXT NOT NULL DEFAULT 'manual'
                       CHECK (added_via IN ('receipt_scan', 'photo_scan', 'voice', 'manual')),
    confidence         TEXT NOT NULL DEFAULT 'high'
                       CHECK (confidence IN ('high', 'medium', 'low')),
    reviewed           INTEGER NOT NULL DEFAULT 0,  -- low-confidence rows surface until a human confirms
    source_ref         TEXT,           -- provenance: which scan/dump created this
    notes              TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_items_product_location ON items(product_id, location_id);
CREATE INDEX idx_items_household_status ON items(household_id, status);

-- Receipts + lines: purchase history. Feeds price tracking, budgeting,
-- and auto-restock (buying something again bumps its item back to 'plenty').
CREATE TABLE receipts (
    id             TEXT PRIMARY KEY,
    household_id   TEXT NOT NULL REFERENCES households(id),
    store          TEXT,
    purchase_date  TEXT,               -- ISO date, or YYYY-MM when the receipt is undated
    total          REAL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE receipt_lines (
    id            TEXT PRIMARY KEY,
    receipt_id    TEXT NOT NULL REFERENCES receipts(id),
    product_id    TEXT REFERENCES products(id),  -- null when the line isn't inventory (fees, donations)
    raw_text      TEXT NOT NULL,       -- literal receipt line, kept for parser debugging
    name          TEXT NOT NULL,       -- normalized/expanded
    quantity      REAL NOT NULL DEFAULT 1,
    unit_price    REAL,
    line_total    REAL,
    is_inventory  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_receipt_lines_receipt ON receipt_lines(receipt_id);
CREATE INDEX idx_receipt_lines_product ON receipt_lines(product_id);

-- ---------------------------------------------------------------------------
-- Views: the three queries that solve the actual grocery/meal-planning problem
-- ---------------------------------------------------------------------------

-- Shopping list: anything not comfortably stocked.
CREATE VIEW shopping_list AS
SELECT i.household_id, p.name, p.brand, c.name AS category,
       i.status, i.quantity, i.unit, l.name AS location
FROM items i
JOIN products p ON p.id = i.product_id
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN locations l ON l.id = i.location_id
WHERE i.status IN ('low', 'out')
ORDER BY i.status DESC, c.name;

-- Use-it-up: expiring within 7 days (label dates or estimates).
-- YYYY-MM expirations are treated as the 1st of that month.
CREATE VIEW expiring_soon AS
SELECT i.household_id, p.name, p.brand, i.expiration, i.expiration_source,
       l.name AS location,
       CAST(julianday(CASE WHEN length(i.expiration) = 7
                           THEN i.expiration || '-01'
                           ELSE i.expiration END) - julianday('now') AS INTEGER) AS days_left
FROM items i
JOIN products p ON p.id = i.product_id
LEFT JOIN locations l ON l.id = i.location_id
WHERE i.expiration IS NOT NULL AND i.status != 'out'
  AND days_left <= 7
ORDER BY days_left;

-- Price history: what we've paid for each product over time.
CREATE VIEW price_history AS
SELECT p.name, p.brand, r.store, r.purchase_date,
       rl.quantity, rl.unit_price, rl.line_total
FROM receipt_lines rl
JOIN products p ON p.id = rl.product_id
JOIN receipts r ON r.id = rl.receipt_id
WHERE rl.is_inventory = 1
ORDER BY p.name, r.purchase_date;

-- Review queue: scanner output a human should eyeball before trusting.
CREATE VIEW review_queue AS
SELECT i.id AS item_id, p.name, i.confidence, i.added_via, i.source_ref, i.notes
FROM items i
JOIN products p ON p.id = i.product_id
WHERE i.reviewed = 0 AND i.confidence != 'high';
