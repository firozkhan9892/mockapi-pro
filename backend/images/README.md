# Images Module

Technical reference for the `/api/v1/images` generation API (Phase 1 + Phase 2).

## Folder structure

```
backend/
└── images/
    ├── __init__.py              # exports images_bp
    ├── models.py                # SQLite access + ImageRecord model + DB constants
    ├── schemas.py               # request validation
    ├── storage.py               # disk storage + path-traversal-safe helpers
    ├── utils.py                 # ids, placeholder PNG, backend-module lookup
    ├── service.py               # business logic (generate/list/get/delete)
    ├── routes.py                # Flask Blueprint + HTTP handlers
    └── providers/
        ├── __init__.py
        ├── base.py              # BaseProvider, GenerationStatus, ProviderResult
        ├── config.py            # ProviderConfig (env-driven)
        ├── flux.py              # FluxProvider (HTTP client)
        ├── placeholder.py       # PlaceholderProvider (dev fallback)
        └── registry.py          # get_provider(model) + REAL_PROVIDERS map
```

## Architecture

```
HTTP request
  └─ routes.py            (auth reuse, 400/401/404/502, JSON)
     └─ service.py         (transactional lifecycle: create row -> provider -> save file -> update)
        ├─ models.py       (DB: ai_images table)
        ├─ storage.py      (disk: storage/images, traversal-safe)
        └─ providers/      (provider abstraction; pluggable per model)
            └─ registry.py -> FluxProvider | PlaceholderProvider
```

Key design points:

- **Auth is reused, not duplicated** via `utils.get_backend()` which locates the running
  app module (`app` under gunicorn, `__main__` under `python app.py`) and calls the
  existing `image_requester` / `image_usage_check` / `is_user_active` / `is_admin_user`
  helpers. API-key requests are metered against the daily limit; session requests are not.
- **Providers are pluggable.** `service.generate_image` calls `get_provider(model)` and
  consumes a `ProviderResult`. Swapping Flux for another backend (ComfyUI, RunPod,
  Replicate, OpenAI-compatible) only requires adding a provider class + a registry entry.
- **Graceful degradation.** When a real provider is not env-configured (no `FLUX_API_URL`),
  the registry returns a `PlaceholderProvider` so the full flow stays exercisable in tests
  and local runs without a live AI backend. When configured, `FluxProvider` returns
  terminal `completed`/`failed` results (and a `502` with a logged error on upstream failure).
- **No model auto-download.** The provider only makes HTTP calls to the configured
  endpoint; it never installs or downloads model weights.
- The module is a dependency-free package imported and registered at runtime in
  `backend/app.py` (blueprint registration appended after `init_db()`):
  ```python
  from images import images_bp, models as image_models
  image_models.init_image_table()
  app.register_blueprint(images_bp)
  ```
  No existing route, model, or test was modified beyond that append.

## API endpoints

Base path: **`/api/v1/images`**.

| Method   | Path              | Auth      | Purpose                | Success        |
|----------|-------------------|-----------|------------------------|----------------|
| POST     | `/generate`       | user+key  | generate an image      | `201`          |
| GET      | `/`               | user+key  | list the caller's images | `200`        |
| GET      | `/<image_id>`     | owner+key | download the image     | `200` (PNG)    |
| DELETE   | `/<image_id>`     | owner+key | delete an image        | `200`          |

All routes enforce authentication via `image_requester()` (session **or**
`x-api-key` / `Authorization: Bearer <key>`). Inactive accounts are rejected;
cross-user access returns `404` (existence hidden). Admins may read/delete any
image. API-key-authenticated requests are counted against the caller's daily limit.

### POST /api/v1/images/generate

#### Request

| field            | type   | required | default        | notes                                  |
|------------------|--------|----------|----------------|----------------------------------------|
| `prompt`         | string | yes      |                | 1–2000 chars                           |
| `negative_prompt`| string | no       | `""`           | 0–2000 chars                           |
| `model`          | string | no       | `flux-schnell` | one of `flux-schnell`, `stable-diffusion-xl`, `dall-e-3` |
| `width`          | int    | no       | `1024`         | 64–1024; booleans rejected             |
| `height`         | int    | no       | `1024`         | 64–1024; booleans rejected             |

```bash
curl -X POST http://localhost:5000/api/v1/images/generate \
  -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a red fox","negative_prompt":"blur","model":"flux-schnell","width":512,"height":512}'
```

#### Response 201

```json
{
  "success": true,
  "image": {
    "id": "a1b2c3...",
    "user_id": "d4e5f6...",
    "prompt": "a red fox",
    "negative_prompt": "blur",
    "model": "flux-schnell",
    "width": 512,
    "height": 512,
    "status": "completed",
    "filename": "4b2a9c_512x512.png",
    "filepath": "storage/images/4b2a9c_512x512.png",
    "created_at": "2026-07-31 12:00:00",
    "url": "/api/v1/images/a1b2c3..."
  }
}
```

### GET /api/v1/images

```json
{"images": [ { "id": "...", "url": "/api/v1/images/...", ... } ], "total": 1}
```

Ordered by `created_at DESC`.

### GET /api/v1/images/<image_id>

Streams the stored PNG (`Content-Type: image/png`). Returns `404` if the image
is missing, owned by another user (non-admin), or not yet materialized
(`status != completed` / `filename` is NULL).

### DELETE /api/v1/images/<image_id>

```json
{"success": true}
```

Removes the file from disk (best-effort) and the DB row. Returns `404` for
missing/unauthorized IDs. Deleting an image whose file is already gone still
succeeds (the record is removed).

## Status flow

| status     | when                                                                 |
|------------|----------------------------------------------------------------------|
| processing | row just created, generation in flight (transient)                   |
| completed  | provider returned bytes; file saved; row updated                      |
| failed     | provider failed/timed out; row marked failed; no file kept            |

A row is created as `processing` before the provider call. On success it becomes
`completed`; on provider failure it becomes `failed` and the route returns a
`502` with a concise, logged error. Because the route is synchronous, a `GET`
issued while a long remote job is in flight may observe a `processing`/`failed`
row → returns `404 "Image file missing"`.

## Database schema

```sql
CREATE TABLE ai_images (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT DEFAULT '',
    model TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    filename TEXT,
    filepath TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

All access uses parameterized queries (`?` placeholders, including for
`LIMIT/OFFSET`), a fresh connection per operation with `timeout=10`, and the
database runs in WAL mode (set by the existing `init_db()`).

## Storage flow

- Physical root: `backend/storage/images/`.
- Names are server-generated and globally unique: `{uuid4.hex}_{width}x{height}.png`.
- `storage.save(data, filename)` writes bytes and returns a forward-slash
  normalized relative path (`storage/images/<name>.png`).
- Every filename passed to `storage.resolve` / `save` / `remove` is validated:
  must be a plain basename, must not escape `storage/images/` (traversal blocked
  via `Path.resolve()` + root containment check, and rejects `.`, `''`,
  subdirectories, and `..`).
- `storage.remove` swallows `OSError`/`ValueError` so a missing or already-removed
  file never turns a delete into a `500`.

## Provider architecture

### BaseProvider / ProviderResult / GenerationStatus
```python
@dataclass
class ProviderResult:
    status: GenerationStatus          # PROCESSING | COMPLETED | FAILED
    image_bytes: Optional[bytes] = None
    remote_id: Optional[str] = None
    error: Optional[str] = None

class BaseProvider:
    name: str = "base"
    configured: bool = True
    def generate(prompt, negative_prompt, width, height, model) -> ProviderResult: ...
```

`generate` is **synchronous from the caller's view**: it blocks (polling the
remote job when needed) until terminal. The route therefore always gets a final
`ProviderResult`.

### ProviderConfig (env-driven)
| var                    | default            | notes                                |
|------------------------|--------------------|--------------------------------------|
| `<PREFIX>_API_URL`     | (empty)            | required to activate a real provider |
| `<PREFIX>_API_KEY`     | (empty)            | sent as `Authorization: Bearer ...`  |
| `<PREFIX>_GENERATE_PATH` | `/v1/generate`   | POST JSON body                       |
| `<PREFIX>_STATUS_PATH`   | `/v1/status/{id}`| GET, replace `{id}`                  |
| `<PREFIX>_IMAGE_PATH`    | `/v1/image/{id}` | GET raw image bytes                  |
| `<PREFIX>_TIMEOUT`       | `60`             | total seconds                        |
| `<PREFIX>_POLL_INTERVAL` | `2`              | seconds between polls                |

`.env` is loaded from the project root if `python-dotenv` is installed.

### FluxProvider
Selected for model `flux-schnell` (env prefix `FLUX`). Reads `FLUX_API_URL`/
`FLUX_API_KEY`. If `FLUX_API_URL` is unset, `configured` is `False` and the
provider is skipped by the registry (see below). Supports:
- **Sync completion**: `200` with JSON `{"image": "<base64>"}` (or raw PNG bytes)
  → `COMPLETED`.
- **Async jobs**: `202` with `{"id": "..."}` → polls `STATUS_PATH` until
  `completed`/`succeeded` or `failed`/`error`/`ERROR`, then fetches bytes via
  `IMAGE_PATH` (or decodes an embedded image) → `COMPLETED`.
- On HTTP errors / connection errors / timeout → `FAILED` with a logged message.

### Registry dispatch
```python
REAL_PROVIDERS = {
    "flux-schnell": lambda: FluxProvider(ProviderConfig.from_env("FLUX")),
    ...
}

def get_provider(model) -> BaseProvider:
    factory = REAL_PROVIDERS.get(model)
    if factory and factory().configured:
        return factory()
    return PlaceholderProvider()   # dev/test fallback
```

### PlaceholderProvider
Dev/test fallback (used automatically when no real provider is configured). Reuses
`images.utils.make_placeholder_png` — a dependency-free PNG seeded from the prompt.
It always returns `COMPLETED` with valid PNG bytes. **Not for production use.**

### Adding a provider
1. Subclass `BaseProvider`, read a `ProviderConfig(prefix)`.
2. Add a `lambda` entry to `REAL_PROVIDERS` in `images/providers/registry.py`.
3. That's it — existing routes/validation/storage are untouched.

## Error codes

| code | meaning                                                       |
|------|---------------------------------------------------------------|
| 200  | OK (list / get file / delete success)                         |
| 201  | Image generation succeeded                                    |
| 400  | Validation error (missing/blank/overlong prompt, bad model, size out of range, non-integer, width==height parity optional) |
| 401  | Unauthenticated / inactive account                            |
| 404  | Image not found / not owned by caller / not yet materialized   |
| 413  | n/a (JSON body; no file upload on this API)                   |
| 429  | Daily request-limit reached (API-key callers)                 |
| 502  | Upstream provider failure                                     |
| 500  | Unexpected server error                                        |

## Validation rules

- `prompt` required, non-empty after strip, ≤ 2000 chars.
- `negative_prompt` optional, ≤ 2000 chars (blank → `""`).
- `model` ∈ {`flux-schnell`, `stable-diffusion-xl`, `dall-e-3`}; missing → `flux-schnell`.
- `width` / `height`: integers, **not booleans**, in [64, 1024]; default 1024.
- Body must be a JSON object (non-dict → 400).
- `width` and `height` must have the same parity (both even) for compatibility
  with most diffusion backbones. (If parity differs, FluxProvider may return a
  400/502 from the upstream; the API itself does not enforce parity.)

## Frontend

The `/images` page (`frontend/images.html`) now hosts both features in one
screen, using the existing dark theme and auth flow (`/api/me` session check):

- **Upload Images** tab (unchanged): drag-and-drop zone, file picker, grid of the
  caller's uploaded images, per-image *Copy URL* / *Delete*, owner-scoped +
  admin override.
- **AI Image Generator** tab (new): prompt / negative-prompt / model dropdown /
  width × height selectors (64–1024) / **Generate Image** button with spinner,
  a generated-image preview with status badge, *Download* and *Delete* buttons,
  and a **Your Generations** history gallery below.

Auth is reused via session cookies (`credentials: 'include'`); the API key /
metering behaviour is enforced server-side, exactly as for `/api/images`. The
existing upload functionality was preserved verbatim (same routes, same
`/api/images` endpoints) — only the surrounding UI was unified.

## Environment variables (production)

Set these before starting the server to enable real Flux generation:

```bash
export FLUX_API_URL="https://your-flux-endpoint.example.com"
export FLUX_API_KEY="your-bearer-token"            # if required by the endpoint
export FLUX_GENERATE_PATH="/v1/flux"               # override defaults as needed
export FLUX_STATUS_PATH="/v1/flux/{id}"
export FLUX_IMAGE_PATH="/v1/flux/{id}/png"
export FLUX_TIMEOUT="90"
export FLUX_POLL_INTERVAL="3"
```

When these are absent, generation falls back to the placeholder provider.

## Testing

| file                         | scope                                                   |
|------------------------------|---------------------------------------------------------|
| `test_imagegen_unit.py`      | schemas, placeholder PNG, storage/traversal (no server) |
| `test_imagegen.py`           | full HTTP flow: auth, validation, CRUD, ownership, API-key usage, admin override |
| `test_imagegen_providers.py` | provider registry, FluxProvider HTTP (sync/async/fail) via stubbed requests, placeholder fallback |

Run on a clean DB:
```bash
python -m pytest          # or each file: python test_*.py
```

## Extension points

- New model provider → subclass `BaseProvider` + add to `REAL_PROVIDERS`.
- New upstream API shape → override `FluxProvider`/add a provider using
  `ProviderConfig.from_env(<PREFIX>)`.
- New status (e.g. `queued`) → handled inside the provider, not the routes.
- New image metadata field → add to `ai_images`, `ImageRecord`, `to_dict()`,
  and `schemas` as needed; routes are already generic.
