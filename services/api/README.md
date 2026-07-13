# HealthCore Incidents API

## Run

```bash
cd services/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Endpoints

- `POST /api/incidents/analyze`
  - multipart/form-data with `file`
  - Returns analysis summary in JSON.
- `GET /api/incidents/results/export`
  - Returns latest analysis in CSV format.
