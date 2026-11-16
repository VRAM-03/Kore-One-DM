# nexyn-backend
Connector based DMP 

## Device time settings

Use the same Python environment to install dependencies and run the backend.
For example, on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

`tzdata` supplies IANA timezone validation on Windows and other systems without
a system timezone database. Configure the existing `COIOTE_BASE_URL`,
`COIOTE_USERNAME` and `COIOTE_PASSWORD` before starting the server.
If the backend's interpreter lacks timezone data, timezone updates return 503
with installation guidance, not a misleading invalid-timezone response.

Use **PATCH `/devices/{endpoint_name}/time`** in Swagger `/docs`.
The endpoint name selects a locally known device and its stored Coiote ID.
Any combination of these fields can be supplied:

```json
{
  "timezone": "Asia/Kolkata",
  "utc_offset": "+05:30",
  "current_time": "2026-11-16T17:50:00+05:30"
}
```

- `timezone`: valid IANA timezone name.
- `utc_offset`: signed `HH:MM` offset, with hours 00-23 and minutes 00-59.
- `current_time`: ISO 8601 timestamp including `Z` or an explicit offset,
  at whole-second precision. Converted to UTC before writing.
- Omitted fields are left unchanged and are not written or read back.
- Send `{}` (or no request body) to read all three settings without writing.
- Explicit nulls, unknown field names, invalid timezones, and naive/numeric/
  fractional timestamps are rejected with 422.

The backend uses the documented
[Coiote v3 API](https://eu.iot.avsystem.cloud/apidoc/coiotedm/v3#/):

1. `POST /api/coiotedm/v3/tasks/configure/{deviceId}` submits sequential
   writes and reads for `Device.0.Timezone` (`/3/0/15`),
   `Device.0.UTC Offset` (`/3/0/14`), and
   `Device.0.Current Time` (`/3/0/13`).
2. `GET /api/coiotedm/v3/taskReports/{taskId}/{deviceId}` is polled internally.
3. After success, `GET /api/coiotedm/v3/cachedDataModels/{deviceId}` retrieves
   the selected resources and checks that every value is fresh for this task.

One application request returns the task ID, requested fields, device-reported
`values`, and a per-field `observed_at` timestamp. No caller task polling is
needed. Current time naturally advances after being set, so its readback is not
required to equal the submitted instant exactly. Timezone and offset readbacks
are compared to the supplied strings.

Timezone and UTC offset are independent device resources; the backend does not
derive one from the other or change omitted values automatically. Device firmware
may apply its own timezone rules. These resources are optional in LwM2M, so the
device must implement the fields being accessed; reading an empty body requires
all three.

Responses: 200 on success, 409 for a timezone/offset readback mismatch or
unregistered device, 404 for a missing device/task, 422 for invalid input, 502
for Coiote failure or malformed results, 503 for missing server timezone data,
and 504 after a 30-second deadline.
A partial failure or timeout does not roll back device writes; they may already
have applied or may execute later. Read with `{}` before retrying.
Automated tests mock Coiote and never change a real device.
