# KORE One DM Prototype

Angular frontend for FastAPI device CRUD, inventory and device communication APIs.
The layout follows the supplied KORE Wireless console references: a light-gray
sidebar, blue outlined actions, bright-blue table links, thin grid borders,
compact badges and structured details. The logo and favicon use the orange/red
O extracted from the user-supplied KORE image, alongside locally bundled Lucide
SVG icons. Devices is the initial view; dashboard
counts are separate. Promotional panels and repeated explanatory text are omitted.
Subtle droplet/ripple transitions respect `prefers-reduced-motion`.

## Project structure

```text
backend/                 FastAPI application, Coiote connector and SQLite models
  tests/                 Backend API and connector regression tests
  requirements.txt       Python dependencies
frontend/                Angular application and its npm dependencies
PROJECT_CONTEXT.md       Product decisions, implementation history and API research
DEVICE_LIFETIME.md       Detailed registration-lifetime behavior and limitations
.env                     Local Coiote configuration (not tracked)
nexyn.db                 Local SQLite data (not tracked)
```

The database remains at the repository root; the database configuration and
models are in `backend/database.py` and `backend/models.py`. Run backend commands
from the repository root so the existing relative SQLite path continues to use
the same database. Do not delete the database or `.env` as part of routine cleanup.

## Run locally

Use Python 3.14 and create a virtual environment if you don't already have one:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r backend/requirements.txt
```

Create a root `.env` with your own Coiote account values:

```text
COIOTE_BASE_URL=<your-coiote-base-url>
COIOTE_USERNAME=<your-coiote-username>
COIOTE_PASSWORD=<your-coiote-password>
```

Never commit or share the real values. Existing `.env` and `nexyn.db` files
can be reused; the database schema is updated additively on application startup.

Use **Linux Node.js 22.22.3 or newer in the 22.x line**, or another version supported
by Angular 22. Check `node --version` and `npm --version` inside WSL. Do not use the
Windows npm executable to install dependencies in a WSL filesystem.

Start the existing backend in one terminal, from the repository root:

```bash
venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm start
```

Open **http://localhost:4200**.

The app requires sign-in using the existing KORE development SSO server and
the `chts-diagnostic-tool-dev` client in the `KORE-CIDP` realm. The shared
client must allow `http://localhost:4200/*` as a redirect URI and
`http://localhost:4200` as a web origin, support browser-based authorization
code flow with PKCE, and allow the user's account. Keep using `localhost`
rather than `127.0.0.1` for the frontend; they are different origins. If sign-in
or initialization fails, startup stops and the browser console reports the
error. This development SSO configuration requires access to the existing server;
no local Keycloak server or client secret is needed.

The development proxy forwards `/api/*` to `http://127.0.0.1:8000`, removing
the `/api` prefix. Restart the backend after updating so the current API routes
and additive database migrations are loaded. No CORS changes are required.
If your backend uses a different address, change `frontend/proxy.conf.json` and restart
Angular. The API-documentation link targets the default backend address.

## Scope

- Shared inventory uses connector-neutral terminology. Device name displays the
  existing `endpoint_name`; API fields and links are unchanged. The old onboarding
  state badge is removed: Onboarding time (UTC) shows `first_registered_at`, and
  Status shows `last_seen_at`, explicitly labelled Last seen (UTC). Missing
  registration is shown as a dash; missing contact as No contact recorded.
  These are stored timestamps, not verified live online/offline states.
  Provider names/settings remain in connector-specific areas.
- The header shows a Super Admin banner below Kore One DM Prototype for the concept demo.
  The label is not derived from Keycloak and does not grant or enforce roles.
  Keycloak login gates frontend startup, but the backend does not validate tokens
  or enforce authorization; it remains accessible directly.
- The desktop sidebar collapses to an icon rail and expands using the header
  toggle. Icons retain accessible names and tooltips. Mobile keeps its separate
  navigation drawer; collapse state lasts only for the current page session.
- Expanded sidebar branding is a compact O icon with One, aligned top-left
  and vertically centered. The collapsed rail keeps the O icon alone.
- Dashboard counts derived from the current device inventory (no invented online
  status), plus a connector information view.
- Search by device/friendly name, creation-time sorting and pagination are
  client-side. Devices default to newest-created-first; the Created time (UTC)
  arrow switches to oldest-first. Equal timestamps use ID ordering; missing
  creation timestamps stay last in both directions. No provider request is made.
- Choose a connector before creating a device. AVSystem Coiote is currently the
  only available connector; selecting it reveals security mode and PSK fields.
  Endpoint and friendly name are shared fields. Deselecting the connector clears
  secrets and resets security mode to PSK. Create supports PSK or no-security
  test mode.
- Click an endpoint to open `/devices/{endpoint}` with Overview and
  Settings & configuration sections. Overview shows stored administrative details, registration
  timestamps and a filtered stored Coiote snapshot, not live resource values.
- Read timezone explicitly, then use Check result to retrieve the operation outcome. Resource discovery
  checks Coiote's stored model capabilities; it is not itself a live Read.
- Read the device timezone before editing it. Apply to device submits a
  timezone Write followed by Read-back verification. The UI shows
  Applied and verified only after the backend confirms a matching returned value.
- Registration lifetime and eight allowlisted communication settings are under a
  collapsible LwM2M server section; Timezone remains a separate setting. Lifetime
  can be read and written, with fresh read-back verification for writes. Reads
  reuse the latest stored confirmed observation when available, which may be
  stale. See [DEVICE_LIFETIME.md](DEVICE_LIFETIME.md) for timeouts and safety.
- The server section includes notification storing, binding, initial registration
  delay, communication retry count/timer, sequence delay/retry count and Mute Send.
  Settings are available according to discovered device capabilities; changes to
  communication settings can interrupt connectivity. Successful writes require a
  matching fresh read-back, not just task acceptance.
- Rename the friendly name only.
- Delete with an exact endpoint-name confirmation. This can delete the remote
  Coiote record too; it does not stop the running device client.
- Reload list/details from the platform database through existing device GETs.
  These requests make no Coiote calls. No separate sync UI action.
- No group operations in the frontend, arbitrary device writes, logout control,
  CMP functionality, or direct access to Coiote credentials.

| Frontend request                 | Existing backend route       |
| -------------------------------- | ---------------------------- |
| `GET /api/health`                | `GET /health`                |
| `GET /api/devices`               | `GET /devices`               |
| `POST /api/devices`              | `POST /devices`              |
| `GET /api/devices/{endpoint}`    | `GET /devices/{endpoint}`    |
| `PATCH /api/devices/{endpoint}`  | `PATCH /devices/{endpoint}`  |
| `DELETE /api/devices/{endpoint}` | `DELETE /devices/{endpoint}` |
| `GET /api/devices/{endpoint}/resources` | `GET /devices/{endpoint}/resources` |
| `POST /api/devices/{endpoint}/reads` | `POST /devices/{endpoint}/reads` |
| `POST /api/devices/{endpoint}/writes` | `POST /devices/{endpoint}/writes` (approved addition) |
| `GET /api/devices/{endpoint}/operations/{id}` | `GET /devices/{endpoint}/operations/{id}` |
| `POST /api/devices/{endpoint}/lifetime/read` | `POST /devices/{endpoint}/lifetime/read` |
| `PATCH /api/devices/{endpoint}/lifetime` | `PATCH /devices/{endpoint}/lifetime` |
| `GET /api/devices/{endpoint}/server-settings` | `GET /devices/{endpoint}/server-settings` |
| `POST /api/devices/{endpoint}/server-settings/{resourceId}/read` | `POST /devices/{endpoint}/server-settings/{resourceId}/read` |
| `PATCH /api/devices/{endpoint}/server-settings/{resourceId}` | `PATCH /devices/{endpoint}/server-settings/{resourceId}` |
| `GET /api/devices/{endpoint}/server-settings/operations/{id}` | `GET /devices/{endpoint}/server-settings/operations/{id}` |

## Important behavior

- Connector selection is a frontend provider choice, not the backend's
  `connector_type` protocol metadata. It sends the unchanged device-create
  payload to the existing Coiote-backed API; no other provider or dispatch route
  is introduced. Coiote account credentials remain configured on the backend.
- List/detail GETs read SQLite only; they do not rediscover devices, import
  provider-only records or fetch provider metadata. Creation already stores
  the returned Coiote ID, so a newly created device is immediately readable.
  Existing local/imported records are retained. Inventory is not polled.
- The header no longer displays backend availability. Diagnostics remain beside
  Check connection in the connector view, labelled Platform API availability,
  not connector authentication or device reachability. Existing `/health` checks
  still run on initial load, inventory reload and explicit checks. Request
  failures remain visible. No mock backend is bundled or enabled by the app.
- The separate Sync from Coiote UI action remains removed. The existing backend
  `POST /sync-devices` still explicitly reconciles provider metadata/imports,
  with sanitized errors and no partial commit; this UI never calls it.
- Stored registration/contact history is not automatically refreshed by page
  loading or Reload. `refreshed_at` remains the last successful provider-metadata
  fetch, not the current request time. Provider-side changes/removals are not
  detected by local GETs. Freshness/event-driven updates need a separate design.
  A new device without a metadata fetch has a null Coiote snapshot/timestamps.
- Failed local inventory loads show an error rather than presenting old rows as
  successfully reloaded.
- Device-page loading makes local administrative/detail and provider
  resource-discovery GETs independently. Discovery still contacts Coiote for
  safe model capabilities but cannot hold up administrative details.
  No live Read or Write is sent until the user requests it.
- Timezone results are checked manually; unresolved or unconfirmed operations
  block another submission on the current page. Its page state is not restored
  after navigation, although backend operations are persisted. The backend blocks
  known unresolved timezone Writes sequentially, not atomically under concurrency.
- Server-setting operations are persisted and recovered on page load. Pending
  results are checked automatically at two-second intervals, up to 30 checks,
  with manual checks available afterward. Checks do not resubmit a command.
  Only one of the eight server-setting operations may be unresolved per device.
  A submission without a confirmed task ID remains blocked and requires
  investigation; do not blindly retry after an unknown outcome.
- The backend also has a `/devices/{endpoint}/time` API for timezone, UTC offset
  and current time. Firmware upload and status endpoints exist but firmware
  submissions are disabled unless `FOTA_ENABLED=true`; they have no frontend UI.
  An enabled firmware upload requires a known stored connector domain; refresh
  the device metadata if it is missing rather than assuming an account domain.
  Firmware operations require separate review, device compatibility and safe
  recovery planning. The current backend is not authorized for public exposure.
- Timezone uses the IANA name in `Device.0.Timezone` (for example `Etc/UTC` or
  `Asia/Kolkata`), limited to 31 UTF-8 bytes for the Anjay demo. Both Read and Write
  capability must be exposed. Account permissions/model mapping still need
  verification with the actual client. Unsupported resources show unavailable.
  This changes the client's LwM2M resource, not the host OS; the demo may reset it
  on restart. A successful Read proves contact only at its completion time.
- Create/update responses can have null or older metadata-fetch timestamps.
  They are displayed as returned. Reload does not update those timestamps.
- A create failure may leave a locally saved record. On network/5xx uncertainty,
  the form prevents repeat submission and clears credential inputs. Close it,
  reload the local list and verify the record/outcome in Coiote before retrying.
- Credentials are password inputs, never saved in local/session storage, and
  cleared when the form closes. They still pass through your browser and the
  existing backend request. Browser developer tools can inspect requests; do not
  share request captures containing secrets.

**Local PoC only:** the backend has no API access control and stores PSKs in
plaintext SQLite. Use test credentials. Do not expose the dev server/backend to
the internet or deploy it to a shared environment without authentication, TLS,
authorization, and secure secret storage.

## Validation

From the repository root:

```bash
venv/bin/python -m unittest -q
cd frontend
npm run build
npm test -- --watch=false
```

Tests cover existing API methods/payloads, backend reconnection, search/pagination, credential validation
and clearing, uncertain submissions, delete confirmation, failure handling,
details, routing, manual operation checks, Read-before-Write, verified settings,
uncertainty without retries, stale-response isolation, and inventory refresh.
They use mocks and do not contact Coiote.

The frontend production build is in `frontend/dist/nexyn-console/browser`. The development proxy
is **not** part of a production build: a deployment needs a same-origin reverse
proxy that strips `/api` and forwards it to FastAPI, plus a SPA fallback to
`index.html` for frontend device deep links. No deployment configuration is introduced here.

Icons are from `lucide-static`, distributed under the ISC license. Selected SVGs
and the required license are bundled in `frontend/public/icons`. No external icon CDN,
font service, or analytics is used.
