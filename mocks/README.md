# mocks/

One FastAPI service (Compose service `mocks`, port 9000) that stands in for the systems Relay integrates with (PRD 15.2). TBO's mocks replace these system by system when the starter kit arrives. Our extensions (`statutory`, `calendar`, `notify`) only add endpoints.

```bash
uvicorn mocks.app:create_app --factory --port 9000
```

`mocks/systems.py` is the single table of paths, payloads and modes. OpenAPI is at `/docs`, but request bodies are validated by hand, so read the models in `systems.py` for their shapes.

## Systems

| System | Collection | Create op | Other ops | Idempotency mode |
|---|---|---|---|---|
| `directory` | `accounts` | `create_account` | `disable_account` (`POST .../{id}/disable`) | `header` |
| `assets` | `laptops` | `allocate_laptop` | `recover_laptop` (`.../recover`) | `header` |
| `access` | `cards` | `issue_card` | `revoke_card` (`.../revoke`) | `reference_lookup` |
| `payroll` | `employees` | `setup_employee` | `notify_exit` (`.../exit`) | `natural_key` (`employee_code`) |
| `ticketing` | `tickets` | `create_ticket` | `comment` (`.../comments`) | `header` |
| `statutory` | `pf` | `register_pf` | | `natural_key` (`employee_code`) |
| `calendar` | `events` | `create_events` | | `reference_lookup` |
| `notify` | `messages` | `send` (`channel: slack\|email`) | | `header` |

Every system also serves `POST /{system}/{collection}`, `GET /{system}/{collection}?field=value` (returns `{items, count}`; `client_reference` is the reconcile lookup), `GET /{system}/{collection}/{id}` and `GET /{system}/health`.

## Idempotency modes

- `header`: the same `Idempotency-Key` and body replays the stored response with `Idempotent-Replayed: true`. The same key with a different body is 422.
- `reference_lookup`: no protection. A repeat creates a duplicate, so the connector must look up `client_reference` before retrying.
- `natural_key`: a second create for the same key is 409 with `{"error": "already_exists", "existing_id": ...}`.

## Fault injection

Send `X-Fault: <mode>` on a request, or set a standing rule. The header wins over rules.

| Mode | Effect | Side effect applied? |
|---|---|---|
| `503` | 503 with `Retry-After` | no |
| `timeout` | hangs `MOCK_TIMEOUT_SECONDS` (default 30), then 504 | no |
| `timeout_after_commit` | hangs, then 504: a lost acknowledgement | yes |
| `slow` | waits `MOCK_SLOW_SECONDS` (default 3), then normal | yes |
| `duplicate_on_retry` | ignores idempotency, so a retry creates a second resource | yes |

`timeout_after_commit` goes beyond the PRD's four modes, because a lost ack is the case reconcile exists for.

## Control endpoint (demo console and tests)

| Call | Purpose |
|---|---|
| `POST /_control/faults` | Add a rule: `{"system": "assets", "op": "*", "fault": "503", "remaining": 3, "duration_seconds": 60}`. All but `fault` are optional |
| `GET /_control/faults`, `DELETE /_control/faults`, `DELETE /_control/faults/{id}` | List, clear, or remove rules |
| `GET /_control/stats` | Per system: `records`, `requests`, `side_effects`, `replays` |
| `POST /_control/reset` | Clear all data, counters and rules |
