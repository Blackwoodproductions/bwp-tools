# Flower HTTP API — full reference

Source of truth: `flower/urls.py`, `flower/api/tasks.py`, `flower/api/workers.py`, `flower/api/control.py`, `flower/views/monitor.py` (github.com/mher/flower). This file is the skill's institutional memory of those route handlers; if Flower adds or renames endpoints, refresh this file first.

All endpoints require **HTTP Basic Auth** (`--basic_auth` on the Flower server). Unauthenticated callers get `401`. In CADE prod, basic auth is configured and the creds live under `cade-flower` in `skills.settings.local.json`.

Every `POST` endpoint below mutates state. `flower.py` gates them behind `--confirm` (and `--destructive` for worker shutdown).

---

## Table of contents

1. [Task — read](#task--read)
2. [Task — control (writes)](#task--control-writes)
3. [Task — submission (writes)](#task--submission-writes)
4. [Worker — read](#worker--read)
5. [Worker — control (writes)](#worker--control-writes)
6. [Queue — read](#queue--read)
7. [Monitor](#monitor)
8. [Task state values](#task-state-values)
9. [Full `task-info` response shape](#full-task-info-response-shape)
10. [Full `workers` response shape](#full-workers-response-shape)

---

## Task — read

### `GET /api/tasks`

List tasks matching filters.

Query params (all optional):

| Name | Type | Notes |
|---|---|---|
| `limit` | int | max rows |
| `offset` | int | skip first N |
| `sort_by` | enum | `name` \| `state` \| `received` \| `started` |
| `workername` | string | filter by worker; literal `All` = no filter |
| `taskname` | string | filter by task name; literal `All` = no filter |
| `state` | enum | `PENDING` \| `RECEIVED` \| `STARTED` \| `SUCCESS` \| `FAILURE` \| `RETRY` \| `REVOKED`; `All` = no filter |
| `received_start` | string | `%Y-%m-%d %H:%M` (received time > start) |
| `received_end` | string | `%Y-%m-%d %H:%M` (received time < end) |
| `search` | string | free-text across name/args/kwargs/result |

Response: `{ "<task_id>": <task_dict>, ... }` — see [Full `task-info` response shape](#full-task-info-response-shape).

### `GET /api/task/types`

List seen task type names (the set of `name` values Flower has observed).

Response: `{ "task-types": ["tasks.add", "tasks.sleep", ...] }`.

### `GET /api/task/info/{task_id}`

Full dict for one task. Returns `404` if the task isn't in Flower's in-memory state (bounded by `--max_tasks`, default 10k).

Response: see [Full `task-info` response shape](#full-task-info-response-shape).

### `GET /api/task/result/{task_id}`

Get the task's result from the Celery result backend. Requires a backend (`CELERY_RESULT_BACKEND`). Returns `503` if the backend is `DisabledBackend`.

Query params:

| Name | Type | Notes |
|---|---|---|
| `timeout` | float | seconds to wait for a pending result |

Response:

```json
{
  "task-id": "<id>",
  "state": "SUCCESS",
  "result": <json-encodable>,
  "traceback": null
}
```

On `FAILURE`, `result` is the exception repr and `traceback` is populated.

---

## Task — control (writes)

### `POST /api/task/revoke/{task_id}`

Revoke a task. A queued task is simply dropped. A running task is killed iff `terminate=true`.

Query params:

| Name | Type | Default | Notes |
|---|---|---|---|
| `terminate` | bool | `false` | kill it if running |
| `signal` | string | `SIGTERM` | signal to send on terminate — `SIGTERM` (graceful) or `SIGKILL` (force) |

Response: `{ "message": "Revoked '<id>'" }`. Always returns `200` — Celery's revoke is fire-and-forget.

> **Celery behavior:** `revoke` is broadcast to all workers. A worker only acts if the task is in its reserved queue. If the task is on a down worker or already finished, the revoke is a no-op. Set `terminate=true` *before* the worker starts the task to prevent execution; afterward, it interrupts the running process with the signal.

### `POST /api/task/abort/{task_id}`

Abort an `AbortableTask`. Requires the task to subclass `celery.contrib.abortable.AbortableTask` *and* the task body to call `self.is_aborted()` periodically. CADE's tasks do **not** use `AbortableTask`, so this endpoint is rarely useful here — prefer `revoke` with `terminate=true`.

Returns `503` if result backend is disabled.

Response: `{ "message": "Aborted '<id>'" }`.

### `POST /api/task/timeout/{taskname}`

Change soft and/or hard time limits for a task *type* at runtime. Affects all future executions on the target worker(s) until the worker restarts.

Query / form params:

| Name | Type | Notes |
|---|---|---|
| `workername` | string | **required** — target worker |
| `soft` | float | soft time limit seconds |
| `hard` | float | hard time limit seconds |

Returns `404` if `taskname` or `workername` is unknown; `403` on broadcast failure.

### `POST /api/task/rate-limit/{taskname}`

Change the rate limit for a task *type* at runtime.

Query / form params:

| Name | Type | Notes |
|---|---|---|
| `workername` | string | **required** — target worker |
| `ratelimit` | string | **required** — e.g. `"200/m"`, `"10/s"` |

---

## Task — submission (writes)

Three endpoints, increasingly loose about task registration. Body is JSON:

```json
{ "args": [..], "kwargs": {..}, "eta": "...", "countdown": N, "expires": ..., <other apply_async kwargs> }
```

- `eta` format: `%Y-%m-%d %H:%M:%S.%f`
- `expires` can be float seconds or the same datetime format

### `POST /api/task/apply/{taskname}`

Execute and **wait for the result**. Only works if `taskname` is registered in Flower's own Celery app (usually not — Flower rarely imports CADE task modules). Prefer `send-task`.

Response: `{ "state": "SUCCESS", "task-id": "<id>", "result": ..., "traceback": null }`.

### `POST /api/task/async-apply/{taskname}`

Execute without waiting. Same registration requirement as `apply`.

Response: `{ "state": "PENDING", "task-id": "<id>" }`.

### `POST /api/task/send-task/{taskname}`

Send by name — does **not** require the task to be registered on the Flower side. This is what `flower.py task retry` and `task send` use.

Response: `{ "task-id": "<id>", "state": "PENDING" }` (state only if a result backend is configured).

---

## Worker — read

### `GET /api/workers`

List all workers Flower is tracking.

Query params:

| Name | Type | Default | Notes |
|---|---|---|---|
| `refresh` | bool | `false` | run inspect to refresh the list (slow; blocks until `inspect_timeout`) |
| `workername` | string | — | limit to one worker |
| `status` | bool | `false` | return only `{worker: alive_bool}` instead of full dict |

Full response shape: see [Full `workers` response shape](#full-workers-response-shape). With `status=true`, returns `{ "celery@host": true, ... }`.

Returns `404` on an unknown `workername`; `503` if inspect fails.

---

## Worker — control (writes)

### `POST /api/worker/shutdown/{workername}` — **DESTRUCTIVE**

Shuts the worker down. It will deregister from the broker. Celery does not auto-restart; bringing it back requires your orchestrator (systemd, k8s, docker-compose). `flower.py` requires both `--confirm` and `--destructive` for this.

Response: `{ "message": "Shutting down!" }`.

### `POST /api/worker/pool/restart/{workername}`

Restart the worker's process pool (child processes restart; the main worker process keeps its broker connection). Requires the worker to be started with `CELERYD_POOL_RESTARTS=true` — returns `403` with reason otherwise.

### `POST /api/worker/pool/grow/{workername}`

Add child processes.

| Name | Type | Default |
|---|---|---|
| `n` | int | `1` |

### `POST /api/worker/pool/shrink/{workername}`

Remove child processes.

| Name | Type | Default |
|---|---|---|
| `n` | int | `1` |

### `POST /api/worker/pool/autoscale/{workername}`

Change autoscale bounds. Worker must be started with `CELERYD_AUTOSCALER` — returns `403` otherwise.

| Name | Type | Notes |
|---|---|---|
| `min` | int | **required** |
| `max` | int | **required** |

### `POST /api/worker/queue/add-consumer/{workername}`

Make the worker start consuming from an additional queue.

| Name | Type | Notes |
|---|---|---|
| `queue` | string | **required** |

### `POST /api/worker/queue/cancel-consumer/{workername}`

Stop consuming from a queue.

| Name | Type | Notes |
|---|---|---|
| `queue` | string | **required** |

---

## Queue — read

### `GET /api/queues/length`

Broker queue depths — for AMQP uses the management HTTP API, for Redis uses LLEN.

Response:

```json
{ "active_queues": [ {"name": "celery", "messages": 0}, {"name": "keywords", "messages": 5} ] }
```

Returns counts only for queues actively consumed by *known* workers (union of each worker's `active_queues`).

---

## Monitor

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /healthcheck` | none | literal `OK` |
| `GET /metrics` | none | Prometheus text format |

Neither requires basic auth (they subclass `BaseHandler`, not `BaseApiHandler`).

---

## Task state values

Celery states Flower surfaces via the `state` field:

| State | Meaning |
|---|---|
| `PENDING` | unknown to broker (never seen) OR not yet received |
| `RECEIVED` | worker has picked it up but not started |
| `STARTED` | worker is executing (requires `CELERY_TASK_TRACK_STARTED=true`) |
| `SUCCESS` | finished successfully |
| `FAILURE` | raised an unhandled exception |
| `RETRY` | task raised `Retry` and will re-run |
| `REVOKED` | revoked via `POST /api/task/revoke/...` |

> CADE-specific: `CELERY_TASK_TRACK_STARTED` is enabled, so `STARTED` is meaningful. `retries` increments on every `Retry`; it does *not* count the original attempt (`retries=0` means first run, `retries=3` means the task has been retried 3 times so 4 total attempts).

---

## Full `task-info` response shape

Every field from `celery.events.state.Task.as_dict()` plus flower's additions:

| Field | Type | Notes |
|---|---|---|
| `task-id` / `uuid` | string | task UUID (`uuid` is the canonical name in events; `task-id` is added by `TaskInfo` handler) |
| `name` | string | task function name, e.g. `app.workers.keyword_worker.generate_keywords` |
| `state` | string | see [Task state values](#task-state-values) |
| `args` | string | **Python repr**, not JSON — e.g. `"[3, 4]"`. Parse with `ast.literal_eval`. |
| `kwargs` | string | **Python repr**, same caveat |
| `result` | string | Python repr of the return value — only set when `SUCCESS` and backend is reachable |
| `exception` | string \| null | repr of the raised exception |
| `traceback` | string \| null | full Python traceback (multi-line string) |
| `retries` | int \| null | retry count (0 = first attempt, N = Nth retry scheduled) |
| `retried` | float \| null | unix ts of last retry |
| `runtime` | float \| null | seconds the task actually took (wall-clock) |
| `received` | float \| null | unix ts when the worker received it |
| `started` | float \| null | unix ts when the worker started it |
| `succeeded` | float \| null | unix ts on success |
| `failed` | float \| null | unix ts on failure |
| `sent` | float \| null | unix ts when the producer sent it (requires `CELERY_SEND_TASK_SENT_EVENT=true`) |
| `eta` | string \| null | ISO-ish timestamp if the task was scheduled |
| `expires` | string \| null | ISO-ish timestamp if `expires` was set |
| `revoked` | float \| null | unix ts if revoked |
| `worker` | string | hostname, e.g. `celery@seo-acg-worker-01` |
| `routing_key` | string \| null | broker routing key used |
| `exchange` | string \| null | broker exchange used |
| `queue` | string \| null | logical queue (newer Flower versions) |
| `client` | string \| null | celery client id of sender |
| `clock` | int | celery's Lamport logical clock |
| `timestamp` | float | last event timestamp |
| `parent_id` | string \| null | parent task UUID if spawned by another task |
| `root_id` | string \| null | root of the task chain |

> `args` / `kwargs` being repr strings is the #1 gotcha. Use `ast.literal_eval` (safe: only parses literals) — `json.loads` will fail on Python `True`/`None`/single-quoted strings.

---

## Full `workers` response shape

```jsonc
{
  "celery@host": {
    "active_queues": [ { "name": "keywords", "routing_key": "keywords", ... } ],
    "registered":    [ "tasks.add", ... ],   // task types this worker can run
    "conf":          { ... },                // partial Celery config snapshot
    "stats": {
      "clock": "918",                        // celery logical clock
      "pid":   90494,
      "pool": {
        "max-concurrency": 4,
        "max-tasks-per-child": "N/A",
        "processes": [ 90499, 90500, 90501, 90502 ],
        "timeouts": [ 0, 0 ],
        "writes":   { "total": 1, "avg": "100.00%", "all": "100.00%", ... }
      },
      "prefetch_count": 16,
      "broker": { "hostname": "...", "port": 5672, "transport": "redis|amqp", ... },
      "rusage": { "utime": 0.97, "stime": 0.41, "maxrss": 26996736, ... },
      "total":  { "tasks.add": 1 }           // per-task-type execution counter since worker start
    },
    "timestamp": 1438049312.073402           // last heartbeat
  }
}
```

With `status=true`, the response collapses to `{ "celery@host": true|false }` (alive flag).

---

## Gotchas seen in prod debugging

- **A `404` on `/api/task/info/<id>`** does not mean the task never existed — it means Flower's in-memory state rolled it off. Flower only keeps the last `--max_tasks` tasks (10k default). For historical state, query the DB or the Celery result backend.
- **`retries` only counts Celery's `raise self.retry(...)`** — the Celery task-level retry layer. It does **not** count the guardrails restart loop, stage retries, or AI API retries that live in CADE's pipeline. See `MEMORY.md > 4 retry layers` for the layering.
- **`args` / `kwargs` are `repr()` strings, not JSON.** Covered above but worth repeating.
- **`revoke?terminate=true` uses `SIGTERM` by default.** A long-running task in the middle of a non-interruptible syscall (crawl4ai's Playwright session, big pgvector operation) may take a while to die. Use `signal=SIGKILL` if you need it gone now — but any cleanup it was doing is lost.
- **`pool/restart` and `pool/autoscale` require the worker to be configured for them.** CADE's default worker config may not enable `CELERYD_POOL_RESTARTS`. Expect `403` with a reason string — that's a config statement, not a Flower bug.
