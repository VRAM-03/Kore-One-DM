# Device connection lifetime

Lifetime reads use confirmed values stored in the Nexyn database, contacting
Coiote only when no non-null lifetime exists for the device. Writes still send
LwM2M operations through Coiote and verify the device's readback.
Implementation: [device_lifetime.py](backend/device_lifetime.py).

There are exactly **two application endpoints**:

| Action | Application request | User input |
|---|---|---|
| Write | `PATCH /devices/{endpoint_name}/lifetime` | Endpoint name and lifetime in seconds |
| Read | `POST /devices/{endpoint_name}/lifetime/read` | Endpoint name only; no body |

Each action requires **one application request**. For writes and uncached reads,
the backend schedules the Coiote operation, polls its report, and retrieves the
fresh value internally before responding. Cached reads make no Coiote calls.
There is no public task-result endpoint or caller polling.

## Setup

Use the existing `COIOTE_BASE_URL`, `COIOTE_USERNAME`, and `COIOTE_PASSWORD`
backend environment variables. Coiote credentials are never sent to the browser.
The Coiote account needs permission to read the device data model, schedule
configuration tasks, and retrieve task reports.

The backend discovers the Server instance from the device's Coiote data model
(`LwM2M Server.{instance}.Short Server ID`), not a hard-coded instance number.
If the device has multiple Server instances, set `COIOTE_SHORT_SERVER_ID` to
the Short Server ID assigned to this Coiote server (1 through 65535).
The backend matches that value to the reported instance.
This is not necessarily the instance number.

If no unique instance can be identified, the request returns 409 without
writing anything. Populate/refresh the Server object in Coiote first.
The public API does not expose a documented automatic mapping from the Coiote
connection to a particular Server instance on a multi-server device.

Start the application from the repository directory:

```bash
venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000/docs` and expand **Device lifetime**, or use the
registration lifetime controls in the Angular device page. See [README.md](README.md)
for frontend and SSO startup instructions.

## Write lifetime

`PATCH /devices/{endpoint_name}/lifetime`

```json
{"lifetime": 3600}
```

`endpoint_name` identifies the local device; its saved `coiote_device_id`
is used in the remote request. Run the existing `/sync-devices` operation
first if the device exists only in Coiote.

Lifetime must be a JSON integer between 1 and 4294967295 seconds.
Strings, booleans, fractions, and null are rejected with 422.

The backend submits a new task using
`POST /api/coiotedm/v3/tasks/configure/{deviceId}?rerunIfExists=false`.
The operations run sequentially (`batchRequests=false`):

1. Write `LwM2M Server.{instance}.Lifetime`.
2. Execute `LwM2M Server.{instance}.Registration Update Trigger` so the
   device can update its registration with the new lifetime.
3. Read the lifetime back from the device.

The backend waits for the task and readback to complete, then returns **200**
with the verified lifetime. It never returns `accepted` as the final result.
If the trigger or read fails after a successful write, the response is **502**
with `state: failed`; the device may already have changed. Verify its state
through Coiote before retrying, since the read endpoint may return a previously
stored observation.

## Read action

`POST /devices/{endpoint_name}/lifetime/read`

No request body. Returns **200** with the latest non-null `confirmed_lifetime`
from `device_lifetime_tasks`, ordered by `observed_at` and matched to both the
local device ID and its current Coiote device ID. This makes no Coiote requests
and creates no new task. Values confirmed by either a read or a write/readback
can be reused.

If no confirmed value exists (including when all stored lifetimes are null),
the backend schedules a real device **read** through Coiote, waits for completion,
and persists the confirmed result for subsequent reads. Failed or pending tasks
are not treated as confirmed values.

The stored value has no automatic expiry. `observed_at` remains the original
device observation time, and `task_id` identifies that observation's task.
Changes made outside these APIs are not automatically reflected.

In Swagger, execute the read endpoint once with the endpoint name. A frontend
Read button should make this one application request and display `lifetime`.

## Internal Coiote requests

For writes and reads without a stored value, the backend handles these calls;
the application does not need to make them:

1. Read the Coiote data model to discover the correct Server instance.
2. Schedule the fresh read or write/readback configuration task.
3. Poll
`GET /api/coiotedm/v3/taskReports/{taskId}/{deviceId}`.
4. Only after Coiote reports `Success`, retrieve the lifetime from
`GET /api/coiotedm/v3/cachedDataModels/{deviceId}?parameters=...`.

The task and cached-data endpoints are separate Coiote APIs; authentication,
instance discovery, and task-report polling mean there may be more than two
outbound HTTP requests, but still only **one application request per action**.
The backend verifies that the resource's `updateTime` is at least the task's `startTime`;
older cached data is not returned as a confirmed read.

Successful write response:

```json
{
  "task_id": "returned-coiote-task-id",
  "device_id": "remote-device",
  "resource": "LwM2M Server.3.Lifetime",
  "operation": "write",
  "state": "succeeded",
  "requested_lifetime": 3600,
  "lifetime": 3600,
  "matches_requested": true,
  "observed_at": "2026-11-16T07:00:02Z"
}
```

For a read-only task, `operation` is `read`, and `requested_lifetime` and
`matches_requested` are null, including when reusing a write task's readback.
`lifetime` is the stored or newly fetched device-reported value from Coiote.
The returned task ID is diagnostic metadata only; callers do not need to use it.

## Waiting and timeouts

Writes and uncached reads have a **30-second overall Coiote-operation deadline**,
including discovery and task submission. The backend polls at one-second
intervals using asynchronous waits, not a blocking sleep.

| Result | HTTP | Meaning |
|---|---|---|
| succeeded | 200 | Stored or freshly fetched device-reported lifetime available |
| verification_failed | 409 | Fresh read differs from the requested value |
| failed | 502 | Coiote reports Error or Warning; no verified value returned |
| timeout | 504 | Coiote did not provide a fresh result before the deadline |

A timeout returns an explicit `detail`, not `accepted` or a stale lifetime.
Coiote may still execute a submitted task after the HTTP request times out.
Verify the device state through Coiote before retrying a write; a database-first
read may still return an earlier observation.
Ensure the application's HTTP timeout and proxy timeout exceed 30 seconds.

Task metadata and successful read snapshots are persisted in
`device_lifetime_tasks`, created automatically on startup. Task IDs are bound
to the originating local device and Coiote device ID. These records are internal;
the read endpoint reuses the latest confirmed snapshot when available.
Responses disable HTTP caching.

No destructive migration drops any legacy `devices.lifetime` column that may
already exist. That obsolete local-only field is no longer mapped or returned.
The duplicate standalone reference implementation has been removed.

## Errors and limitations

- 404: local device not found or remote task deleted.
- 409: device has no Coiote ID or Server-instance discovery is ambiguous/missing.
- 422: invalid lifetime input.
- 502: Coiote task/HTTP/network failure, invalid report/value, or missing result resource.
- 503: invalid backend Short Server ID configuration.
- 504: no fresh device response within 30 seconds.

Coiote report code 4041 means the task is not yet executed/transient, so the
backend keeps waiting internally; code 4040 means the task no longer exists.
Other upstream errors are not silently retried as pending.

The device must implement the Server object's Lifetime and Registration Update
Trigger resources. A sleeping/offline device may cause a timeout. A successful
read is an observation at `observed_at`, not a guarantee no other client changes
the resource afterward.

Tests in [test_device_lifetime.py](backend/tests/test_device_lifetime.py) mock Coiote HTTP
traffic and use an isolated SQLite database. They do not update a real device.
Live-device validation requires a reachable test device and appropriate Coiote
permissions; no live lifetime write is performed as part of automated tests.

## API reference

- [Coiote API v3](https://eu.iot.avsystem.cloud/apidoc/coiotedm/v3#/)
- [OpenAPI specification](https://eu.iot.avsystem.cloud/apidoc/coiotedm/v3.json)

The configuration-task endpoint documents the exact resource names, sequential
operations, and registration-update trigger used here.
