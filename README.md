# SWAPI People API

FastAPI service that returns all people from [swapi.dev](https://swapi.dev), each with an added numeric `id`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/people` | All people (82), original SWAPI fields + `id`. Protected by `X-API-Key` when `API_KEY` is set. |
| GET | `/health` | Liveness check (no auth). |

Example item (trimmed):

```json
{ "id": 1, "name": "Luke Skywalker", "height": "172", "films": ["https://swapi.dev/api/films/1/"], "url": "https://swapi.dev/api/people/1/" }
```

## Design notes

- **id** is parsed from the person's `url` (`.../people/10/` -> `10`), not from list position. SWAPI ids are not contiguous (id 17 does not exist).
- **Pagination**: follows `next` until `null` (9 pages), with a max-page guard.
- **Errors**: transient upstream errors (5xx, 429, timeouts) are retried 3 times; other 4xx fail fast. Any upstream failure returns `502`, never a partial list.
- **Cache**: in-memory, 5 minutes, so repeated calls do not hit SWAPI each time.
- **Auth**: set the `API_KEY` environment variable to require an `X-API-Key` header. If unset (local dev) the endpoint is open.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# with auth:  set API_KEY=<some-value> before starting
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy

Any host that runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT` works (Render, Railway, Azure App Service, Cloud Run).
Set `API_KEY` as an environment variable on the host. Workato calls the public HTTPS URL.
