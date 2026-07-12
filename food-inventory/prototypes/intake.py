#!/usr/bin/env python3
"""Food inventory intake — one parser, three input paths.

    python intake.py receipt IMG_8560.jpeg
    python intake.py shelf IMG_8538.jpeg --location pantry-cabinet
    python intake.py voice "three boxes of pasta, peanut butter almost gone, out of eggs"
    echo "transcript..." | python intake.py voice -

Every mode returns the same Item JSON (matching schema/schema.md), so the app
layer never cares whether a row came from a receipt, a cabinet photo, or a
voice brain-dump. Receipt mode additionally returns PurchaseEvent rows.

Requires: pip install anthropic  +  ANTHROPIC_API_KEY set.
Add --csv to append items to data/items.csv for Glide import.
"""

import argparse
import base64
import csv
import json
import mimetypes
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-4-8"
FAMILY_ID = "gray-austin"  # every row is family-tagged from day one

ITEM_PROPERTIES = {
    "name": {"type": "string", "description": "Normalized display name, e.g. 'Barilla Rotini'"},
    "brand": {"type": ["string", "null"]},
    "category": {
        "type": "string",
        "enum": ["pasta-grains", "canned", "baking", "condiments", "snacks",
                 "bread-bakery", "beverages", "dairy", "meat", "frozen",
                 "produce", "supplements", "toiletries", "household"],
    },
    "quantity": {"type": "number"},
    "unit": {"type": "string", "enum": ["count", "box", "bag", "jar", "can", "lb", "oz", "pct"]},
    "status": {
        "type": "string",
        "enum": ["plenty", "low", "out"],
        "description": "'low' when visibly running out or the user says 'almost gone'; 'out' when the user says they're out of it",
    },
    "expiration": {"type": ["string", "null"], "description": "YYYY-MM or YYYY-MM-DD if a date is visible on packaging, else null"},
    "expiration_source": {"type": "string", "enum": ["label", "estimated", "unknown"]},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "notes": {"type": ["string", "null"]},
}

ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": ITEM_PROPERTIES,
                "required": list(ITEM_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "store": {"type": ["string", "null"]},
        "purchase_date": {"type": ["string", "null"], "description": "YYYY-MM-DD if printed on the receipt, else null"},
        "total": {"type": ["number", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string", "description": "The literal receipt line, verbatim"},
                    "name": {"type": "string", "description": "Expanded normalized name"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": ["number", "null"]},
                    "line_total": {"type": "number"},
                    "category": {"type": ["string", "null"]},
                    "is_inventory": {"type": "boolean", "description": "False for donations, fees, gift cards"},
                },
                "required": ["raw_text", "name", "quantity", "unit_price", "line_total", "category", "is_inventory"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["store", "purchase_date", "total", "line_items"],
    "additionalProperties": False,
}

SYSTEM = """You are the intake parser for a household food + toiletries inventory app.
Your output seeds a family's inventory, so favor usable rows over perfection:
normalize names to how a person would say them, expand receipt abbreviations
(HEB NAT BNLS SKNLS CHKN B -> H-E-B Natural Boneless Skinless Chicken Breast),
and use the confidence field honestly — 'low' means the app will ask the user
to review the row, which is much better than a confidently wrong entry.
Do not invent items you cannot actually see or that were not mentioned.
Ignore prescription medications entirely. Store-brand knowledge: Reggano,
Specially Selected, Happy Harvest, Dakota's Pride, and Cheese Club are ALDI
brands; H-E-B receipts use dense abbreviations."""


def image_block(path: str) -> dict:
    media_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def parse(client: anthropic.Anthropic, content, schema: dict) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        sys.exit("Request was refused — try a different image.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def parse_receipt(client, image_path: str) -> dict:
    return parse(client, [
        image_block(image_path),
        {"type": "text", "text": "Parse this grocery receipt into structured line items. "
         "Preserve each raw line verbatim in raw_text, expand abbreviations into name, "
         "and split multi-quantity lines (e.g. '2 Ea. @ 2.88') into quantity + unit_price."},
    ], RECEIPT_SCHEMA)


def parse_shelf(client, image_path: str, location: str) -> dict:
    return parse(client, [
        image_block(image_path),
        {"type": "text", "text": f"This is a photo of the household's {location}. "
         "Itemize every food/household product you can identify. Estimate quantity from what's "
         "visible ('pct' unit for partially-used items), read expiration dates only if legible, "
         "and mark anything ambiguous with confidence: low rather than guessing hard."},
    ], ITEMS_SCHEMA)


def parse_voice(client, transcript: str) -> dict:
    return parse(client, [
        {"type": "text", "text": "The user dictated a brain-dump of their inventory. Convert it to items. "
         "Map 'almost gone/running low' to status: low, and 'out of X / we need X' to status: out "
         "with quantity 0. Spoken dictation is messy — resolve fillers and self-corrections "
         f"sensibly.\n\nTranscript:\n{transcript}"},
    ], ITEMS_SCHEMA)


def receipt_items(parsed: dict) -> list[dict]:
    """Project receipt line items into Item rows (the convergence step)."""
    return [
        {
            "name": li["name"], "brand": None, "category": li["category"] or "canned",
            "quantity": li["quantity"], "unit": "count", "status": "plenty",
            "expiration": None, "expiration_source": "estimated",
            "confidence": "high", "notes": f"From receipt: {li['raw_text']}",
        }
        for li in parsed["line_items"] if li["is_inventory"]
    ]


def append_items_csv(items: list[dict], csv_path: Path, added_via: str, location: str, source_ref: str):
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else None
    if not fieldnames:
        sys.exit(f"{csv_path} has no header row")
    next_id = len(rows) + 1
    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for item in items:
            writer.writerow({
                "item_id": f"i{next_id:03d}", "family_id": FAMILY_ID,
                "name": item["name"], "brand": item.get("brand") or "",
                "category": item["category"], "location": location,
                "quantity": item["quantity"], "unit": item.get("unit", "count"),
                "status": item["status"], "expiration": item.get("expiration") or "",
                "expiration_source": item.get("expiration_source", "estimated"),
                "added_via": added_via, "confidence": item["confidence"],
                "source_ref": source_ref, "notes": item.get("notes") or "",
            })
            next_id += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("receipt", help="Parse a receipt photo")
    r.add_argument("image")

    s = sub.add_parser("shelf", help="Itemize a cabinet/fridge photo")
    s.add_argument("image")
    s.add_argument("--location", default="pantry-cabinet")

    v = sub.add_parser("voice", help="Parse a dictated brain-dump ('-' reads stdin)")
    v.add_argument("transcript")
    v.add_argument("--location", default="pantry-cabinet")

    for p in (r, s, v):
        p.add_argument("--csv", type=Path, help="Append parsed items to this items.csv")
        p.add_argument("--source-ref", default=None, help="Provenance tag stored on each row")

    args = ap.parse_args()
    client = anthropic.Anthropic()

    if args.mode == "receipt":
        parsed = parse_receipt(client, args.image)
        items = receipt_items(parsed)
        source_ref = args.source_ref or f"receipt-{parsed.get('purchase_date') or 'undated'}-{(parsed.get('store') or 'store').lower().replace(' ', '-')}"
        location, added_via = "pantry-cabinet", "receipt_scan"
    elif args.mode == "shelf":
        parsed = parse_shelf(client, args.image, args.location)
        items = parsed["items"]
        source_ref = args.source_ref or f"photo-{Path(args.image).stem}"
        location, added_via = args.location, "photo_scan"
    else:
        transcript = sys.stdin.read() if args.transcript == "-" else args.transcript
        parsed = parse_voice(client, transcript)
        items = parsed["items"]
        source_ref = args.source_ref or "voice-dump"
        location, added_via = args.location, "voice"

    print(json.dumps(parsed, indent=2))
    if args.csv:
        append_items_csv(items, args.csv, added_via, location, source_ref)
        print(f"\nAppended {len(items)} items to {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
