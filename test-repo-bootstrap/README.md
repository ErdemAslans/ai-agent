# user-service-test

Minimal Flask user-registration API. Used as the **target repository** for the AI Development Agent demo.

## Endpoints

- `POST /users/register` — register a new user (currently lacks email validation)
- `GET /users` — list registered users
- `GET /health` — healthcheck

## Run Locally

```bash
pip install -r requirements.txt
python app.py
# Server: http://localhost:5000
```

## Tests

```bash
pip install -r requirements.txt
pytest -v
```

All tests currently pass.

## Intentional Gap

`POST /users/register` accepts any string as `email`. The AI Development Agent's job is to add email format validation per **TASK-123**.

Expected after AI fix:
- Invalid email (e.g. `"notanemail"`) → HTTP 400 with `{"error": "Invalid email format"}`
- Valid email → HTTP 201 (existing behavior preserved)
- Updated `test_app.py` covering the new behavior
