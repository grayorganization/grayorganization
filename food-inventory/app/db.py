"""Query layer over inventory.db — every function takes a connection."""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH",
                              Path(__file__).resolve().parent.parent / "inventory.db"))
HOUSEHOLD_ID = "gray-austin"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def inventory_by_location(db, q: str = "") -> list[dict]:
    """Locations with their items, optionally name-filtered."""
    rows = db.execute(
        """SELECT i.id, i.quantity, i.unit, i.status, i.expiration, i.confidence,
                  i.reviewed, i.notes, p.name, p.brand,
                  c.name AS category, l.id AS location_id, l.name AS location
           FROM items i
           JOIN products p ON p.id = i.product_id
           LEFT JOIN categories c ON c.id = p.category_id
           LEFT JOIN locations l ON l.id = i.location_id
           WHERE i.household_id = ? AND i.status != 'out'
             AND (? = '' OR p.name LIKE '%' || ? || '%')
           ORDER BY l.name, p.name""",
        (HOUSEHOLD_ID, q, q),
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["location"] or "Unassigned", []).append(r)
    return [{"location": k, "items": v} for k, v in grouped.items()]


def shopping_list(db) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT i.id, p.name, p.brand, c.name AS category, i.status, i.notes
           FROM items i
           JOIN products p ON p.id = i.product_id
           LEFT JOIN categories c ON c.id = p.category_id
           WHERE i.household_id = ? AND i.status IN ('low', 'out')
           ORDER BY i.status DESC, c.name, p.name""",
        (HOUSEHOLD_ID,),
    ).fetchall()


def expiring_soon(db, days: int = 7) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT i.id, p.name, i.expiration, i.expiration_source, l.name AS location,
                  CAST(julianday(CASE WHEN length(i.expiration) = 7
                                      THEN i.expiration || '-01'
                                      ELSE i.expiration END) - julianday('now') AS INTEGER) AS days_left
           FROM items i
           JOIN products p ON p.id = i.product_id
           LEFT JOIN locations l ON l.id = i.location_id
           WHERE i.household_id = ? AND i.expiration IS NOT NULL AND i.status != 'out'
             AND days_left <= ?
           ORDER BY days_left""",
        (HOUSEHOLD_ID, days),
    ).fetchall()


def review_queue(db) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT i.id, p.name, i.quantity, i.unit, i.confidence, i.added_via,
                  i.source_ref, i.notes, l.name AS location
           FROM items i
           JOIN products p ON p.id = i.product_id
           LEFT JOIN locations l ON l.id = i.location_id
           WHERE i.household_id = ? AND i.reviewed = 0 AND i.confidence != 'high'
           ORDER BY i.confidence, p.name""",
        (HOUSEHOLD_ID,),
    ).fetchall()


def locations(db) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, name FROM locations WHERE household_id = ? ORDER BY name",
        (HOUSEHOLD_ID,),
    ).fetchall()


def categories(db) -> list[sqlite3.Row]:
    return db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()


def counts(db) -> dict:
    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "shopping": one("SELECT count(*) FROM items WHERE status IN ('low','out')"),
        "review": one("SELECT count(*) FROM items WHERE reviewed = 0 AND confidence != 'high'"),
        "items": one("SELECT count(*) FROM items WHERE status != 'out'"),
    }


def recipes_ranked(db) -> list[dict]:
    """Recipes with per-ingredient availability, ranked by fewest missing
    required ingredients (i.e. 'what can I make right now' first)."""
    rows = db.execute(
        """SELECT rs.recipe_id, rs.recipe, r.description, rs.ingredient,
                  rs.optional, rs.on_hand
           FROM recipe_ingredient_status rs
           JOIN recipes r ON r.id = rs.recipe_id
           WHERE rs.household_id = ?
           ORDER BY rs.recipe_id, rs.optional, rs.ingredient""",
        (HOUSEHOLD_ID,),
    ).fetchall()
    by_recipe: dict[str, dict] = {}
    for r in rows:
        rec = by_recipe.setdefault(r["recipe_id"], {
            "id": r["recipe_id"], "name": r["recipe"], "description": r["description"],
            "have": [], "missing": [], "optional_missing": [],
        })
        if r["on_hand"]:
            rec["have"].append(r["ingredient"])
        elif r["optional"]:
            rec["optional_missing"].append(r["ingredient"])
        else:
            rec["missing"].append(r["ingredient"])
    ranked = sorted(by_recipe.values(),
                    key=lambda r: (len(r["missing"]), -len(r["have"])))
    for rec in ranked:
        rec["makeable"] = not rec["missing"]
    return ranked


def set_status(db, item_id: str, status: str):
    db.execute(
        "UPDATE items SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, item_id),
    )
    db.commit()


def mark_reviewed(db, item_id: str):
    db.execute("UPDATE items SET reviewed = 1 WHERE id = ?", (item_id,))
    db.commit()


def delete_item(db, item_id: str):
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
