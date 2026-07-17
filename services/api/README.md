# HealthCore Incidents API

## Run

```bash
cd services/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Seed

```bash
cd services/api
python seed.py
```

## Endpoints

- `POST /api/incidents/analyze`
  - multipart/form-data with `file`
  - Returns analysis summary in JSON.
- `GET /api/incidents/results/export`
  - Returns latest analysis in CSV format.
- `POST /suppliers`
  - Create supplier and return created entity with ID.
- `GET /suppliers`
  - List suppliers. Supports `country` and `category` filters.
- `GET /suppliers/{id}`
  - Get supplier detail by ID.
- `PATCH /suppliers/{id}/rate`
  - Update `monthly_rate` and refresh `updated_at`.
- `PATCH /suppliers/{id}/status`
  - Update supplier status (`active` or `suspended`).
- `DELETE /suppliers/{id}`
  - Delete supplier by ID.
