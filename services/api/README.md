# HealthCore Incidents API

## Run

```bash
cd services/api
python3 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Environment

Create a local env file from the example and set a real JWT secret:

```bash
cd services/api
cp .env.example .env
```

The API loads `services/api/.env` automatically at startup.

## Seed

```bash
cd services/api
python seed.py
```

## Endpoints

### Public

- `GET /api/health`
- `GET /`
- `POST /users`
- `POST /auth/login`
- `POST /auth/token` (OAuth2 password flow for Swagger Authorize)

### Protected (Bearer token required)

- `GET /auth/me`
- `GET /profiles/me`
- `PUT /profiles/me`
- `GET /users`
- `GET /users/{id}`
- `PUT /users/{id}`
- `DELETE /users/{id}`
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

## Auth Flow (manual verification)

1. Register: `POST /users` with `email` and `password` (optional `name`, `phone`, `address`).
2. Login: `POST /auth/login` with `email` and `password`.
  - Optional for Swagger Authorize: use `/auth/token` with `username=<email>` and `password`.
3. Copy `access_token` from response.
4. In `/docs`, click Authorize and use `Bearer <token>`.
5. Call protected routes and verify:
  - Without token => `401`
  - Invalid or expired token => `401`
