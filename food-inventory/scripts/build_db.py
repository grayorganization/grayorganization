#!/usr/bin/env python3
"""Build inventory.db from schema.sql + the seed CSVs in data/.

    python scripts/build_db.py [--out inventory.db]

The flat seed CSVs (one row per shelf item, as parsed from the photos) get
normalized on the way in: unique (name, brand, category) rows become products,
and receipt lines get linked to products by case-insensitive name match.
Rebuilding is idempotent — the output db is recreated from scratch each run.
"""

import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOUSEHOLD_ID = "gray-austin"


def read(name: str) -> list[dict]:
    with (ROOT / "data" / f"{name}.csv").open() as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "inventory.db")
    ap.add_argument("--if-missing", action="store_true",
                    help="Do nothing when the db already exists (deploy-time seeding)")
    args = ap.parse_args()

    if args.if_missing and args.out.exists():
        print(f"{args.out} already exists — leaving it alone")
        return

    args.out.unlink(missing_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.out)
    db.executescript((ROOT / "schema" / "schema.sql").read_text())

    db.execute("INSERT INTO households (id, name) VALUES (?, ?)",
               (HOUSEHOLD_ID, "Gray household"))

    for c in read("categories"):
        db.execute(
            "INSERT INTO categories (id, name, default_shelf_life_days) VALUES (?, ?, ?)",
            (c["category_id"], c["name"], int(c["default_shelf_life_days"]) or None),
        )

    for l in read("locations"):
        db.execute(
            "INSERT INTO locations (id, household_id, name, description) VALUES (?, ?, ?, ?)",
            (l["location_id"], HOUSEHOLD_ID, l["name"], l["description"]),
        )

    # items.csv is flat; split into products (catalog) + items (stock).
    product_ids: dict[str, str] = {}  # lower(name) -> product id
    for n, row in enumerate(read("items"), start=1):
        key = row["name"].lower()
        if key not in product_ids:
            pid = f"p{n:03d}"
            product_ids[key] = pid
            db.execute(
                "INSERT INTO products (id, household_id, name, brand, category_id, default_unit)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (pid, HOUSEHOLD_ID, row["name"], row["brand"] or None,
                 row["category"], row["unit"]),
            )
        db.execute(
            "INSERT INTO items (id, household_id, product_id, location_id, quantity, unit,"
            " status, expiration, expiration_source, added_via, confidence, source_ref, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["item_id"], HOUSEHOLD_ID, product_ids[key], row["location"],
             float(row["quantity"]), row["unit"], row["status"],
             row["expiration"] or None, row["expiration_source"], row["added_via"],
             row["confidence"], row["source_ref"], row["notes"] or None),
        )

    # purchases.csv: one receipt, many lines; link lines to products by name.
    purchases = read("purchases")
    receipts = {}
    for row in purchases:
        rid = row["receipt_id"]
        if rid not in receipts:
            receipts[rid] = True
            total = sum(float(r["line_total"]) for r in purchases if r["receipt_id"] == rid)
            db.execute(
                "INSERT INTO receipts (id, household_id, store, purchase_date, total)"
                " VALUES (?, ?, ?, ?, ?)",
                (rid, HOUSEHOLD_ID, row["store"], row["purchase_date"], round(total, 2)),
            )
        db.execute(
            "INSERT INTO receipt_lines (id, receipt_id, product_id, raw_text, name,"
            " quantity, unit_price, line_total, is_inventory)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["purchase_id"], rid, product_ids.get(row["name"].lower()),
             row["raw_text"], row["name"], float(row["quantity"]),
             float(row["unit_price"]) if row["unit_price"] else None,
             float(row["line_total"]), row["is_inventory"] == "true"),
        )

    for r in read("recipes"):
        db.execute(
            "INSERT INTO recipes (id, household_id, name, description) VALUES (?, ?, ?, ?)",
            (r["recipe_id"], HOUSEHOLD_ID, r["name"], r["description"]),
        )
    for ri in read("recipe_ingredients"):
        db.execute(
            "INSERT INTO recipe_ingredients (id, recipe_id, ingredient, match_pattern, optional)"
            " VALUES (?, ?, ?, ?, ?)",
            (ri["id"], ri["recipe_id"], ri["ingredient"], ri["match_pattern"],
             ri["optional"] == "1"),
        )

    db.commit()
    counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("products", "items", "receipts", "receipt_lines", "recipes")}
    db.close()
    print(f"Built {args.out}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
