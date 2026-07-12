FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The db lives on a mounted volume so deploys don't wipe your inventory.
ENV DB_PATH=/data/inventory.db

# Seed the db from the CSVs on first boot only, then serve.
CMD ["sh", "-c", "python scripts/build_db.py --out $DB_PATH --if-missing && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
