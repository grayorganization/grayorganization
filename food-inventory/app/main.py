"""Food Inventory — minimal mobile-first web app over inventory.db.

    cd food-inventory
    python3 scripts/build_db.py            # once, to seed
    uvicorn app.main:app --reload          # http://localhost:8000

Zero JS dependencies: server-rendered pages + plain forms (POST-redirect-GET).
The capture page (brain dump / photos) calls the Claude API via the same
parser as the CLI prototype — set ANTHROPIC_API_KEY to enable it; the rest of
the app works without it.
"""

import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "prototypes"))
import intake  # noqa: E402  (the shared parser + upsert rules)

from . import db as q  # noqa: E402

app = FastAPI(title="Food Inventory")

# Set APP_PASSWORD when deploying anywhere public — gates the whole app
# behind HTTP Basic (username: anything, e.g. "gray"). Off for local use.
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD:
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, password = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(password, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="inventory"'})
    return await call_next(request)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")

VALID_STATUSES = {"plenty", "low", "out"}


def render(request: Request, template: str, **ctx):
    with q.connect() as db:
        ctx["counts"] = q.counts(db)
    return templates.TemplateResponse(request, template, ctx)


@app.get("/")
def inventory(request: Request, q_: str = "", flash: str = ""):
    with q.connect() as db:
        groups = q.inventory_by_location(db, q_)
        locs = q.locations(db)
        cats = q.categories(db)
    return render(request, "inventory.html", groups=groups, q_=q_,
                  locations=locs, categories=cats, flash=flash)


@app.post("/items/{item_id}/status")
def item_status(item_id: str, status: str = Form(...), back: str = Form("/")):
    if status in VALID_STATUSES:
        with q.connect() as db:
            q.set_status(db, item_id, status)
    return RedirectResponse(back, status_code=303)


@app.post("/items/{item_id}/review")
def item_review(item_id: str, action: str = Form(...)):
    with q.connect() as db:
        if action == "confirm":
            q.mark_reviewed(db, item_id)
        elif action == "remove":
            q.delete_item(db, item_id)
    return RedirectResponse("/review", status_code=303)


@app.post("/items")
def add_item(name: str = Form(...), category: str = Form(...),
             location: str = Form(...), quantity: float = Form(1),
             unit: str = Form("count"), status: str = Form("plenty")):
    item = {"name": name.strip(), "brand": None, "category": category,
            "quantity": quantity, "unit": unit,
            "status": status if status in VALID_STATUSES else "plenty",
            "expiration": None, "expiration_source": "unknown",
            "confidence": "high", "notes": None}
    with q.connect() as db:
        intake.upsert_item(db, item, location=location, added_via="manual",
                           source_ref="app-manual")
        db.commit()
    return RedirectResponse(f"/?flash=Added+{name.strip().replace(' ', '+')}",
                            status_code=303)


@app.get("/shopping")
def shopping(request: Request):
    with q.connect() as db:
        items = q.shopping_list(db)
        expiring = q.expiring_soon(db)
    return render(request, "shopping.html", items=items, expiring=expiring)


@app.get("/capture")
def capture(request: Request, error: str = ""):
    with q.connect() as db:
        locs = q.locations(db)
    return render(request, "capture.html", locations=locs, error=error, results=None)


@app.post("/capture/voice")
def capture_voice(request: Request, transcript: str = Form(...),
                  location: str = Form("pantry-cabinet")):
    return _run_intake(request, mode="voice", location=location, transcript=transcript)


@app.post("/capture/photo")
async def capture_photo(request: Request, photo: UploadFile = File(...),
                        mode: str = Form("shelf"), location: str = Form("pantry-cabinet")):
    suffix = Path(photo.filename or "img.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await photo.read())
        tmp_path = tmp.name
    try:
        return _run_intake(request, mode=mode if mode in ("receipt", "shelf") else "shelf",
                           location=location, image=tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _run_intake(request: Request, *, mode: str, location: str,
                transcript: str = "", image: str = ""):
    try:
        client = intake.anthropic.Anthropic()
        if mode == "voice":
            parsed = intake.parse_voice(client, transcript)
            items, receipt, source_ref = parsed["items"], None, "app-voice"
        elif mode == "receipt":
            parsed = intake.parse_receipt(client, image)
            items, receipt, source_ref = intake.receipt_items(parsed), parsed, "app-receipt"
        else:
            parsed = intake.parse_shelf(client, image, location)
            items, receipt, source_ref = parsed["items"], None, "app-shelf"
    except Exception as e:  # missing API key, network, refusal — surface, don't 500
        return RedirectResponse(f"/capture?error={type(e).__name__}:+{str(e)[:120]}",
                                status_code=303)

    added_via = {"voice": "voice", "receipt": "receipt_scan", "shelf": "photo_scan"}[mode]
    with q.connect() as db:
        results = []
        for item in items:
            outcome = intake.upsert_item(db, item, location=location,
                                         added_via=added_via, source_ref=source_ref,
                                         restock=(mode == "receipt"))
            results.append({"item": item, "outcome": outcome})
        if receipt:
            _record_receipt(db, receipt)
        db.commit()
        locs = q.locations(db)
    return render(request, "capture.html", locations=locs, error="",
                  results=results, mode=mode)


def _record_receipt(db, receipt: dict):
    receipt_id = intake._uid("r")
    db.execute(
        "INSERT INTO receipts (id, household_id, store, purchase_date, total)"
        " VALUES (?, ?, ?, ?, ?)",
        (receipt_id, q.HOUSEHOLD_ID, receipt.get("store"),
         receipt.get("purchase_date"), receipt.get("total")),
    )
    for li in receipt["line_items"]:
        product = db.execute(
            "SELECT id FROM products WHERE household_id = ? AND name = ? COLLATE NOCASE",
            (q.HOUSEHOLD_ID, li["name"]),
        ).fetchone()
        db.execute(
            "INSERT INTO receipt_lines (id, receipt_id, product_id, raw_text, name,"
            " quantity, unit_price, line_total, is_inventory) VALUES (?,?,?,?,?,?,?,?,?)",
            (intake._uid("rl"), receipt_id, product[0] if product else None,
             li["raw_text"], li["name"], li["quantity"], li["unit_price"],
             li["line_total"], li["is_inventory"]),
        )


@app.get("/review")
def review(request: Request):
    with q.connect() as db:
        items = q.review_queue(db)
    return render(request, "review.html", items=items)


@app.get("/cook")
def cook(request: Request, flash: str = ""):
    with q.connect() as db:
        recipes = q.recipes_ranked(db)
    return render(request, "cook.html", recipes=recipes, flash=flash)


@app.post("/cook/need")
def cook_need(ingredient: str = Form(...), category: str = Form("canned")):
    """Put a missing ingredient on the shopping list (item with status 'out')."""
    item = {"name": ingredient.strip(), "brand": None, "category": category,
            "quantity": 0, "unit": "count", "status": "out",
            "expiration": None, "expiration_source": "unknown",
            "confidence": "high", "notes": "added from recipe"}
    with q.connect() as db:
        intake.upsert_item(db, item, location=None, added_via="manual",
                           source_ref="app-recipe")
        db.commit()
    return RedirectResponse(
        f"/cook?flash={ingredient.strip().replace(' ', '+')}+added+to+shopping+list",
        status_code=303)
