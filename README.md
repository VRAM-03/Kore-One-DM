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
the `nexus-av-connector-dev` client in the `KORE-CIDP` realm. The client must
have Client authentication Off (public client), Standard flow enabled, and
allow `http://localhost:4200/*` as a redirect URI and
`http://localhost:4200` as a web origin, support browser-based authorization
code flow with PKCE `S256`, and allow the user's account. Keep using `localhost`
rather than `127.0.0.1` for the frontend; they are different origins. If sign-in
or initialization fails, startup stops and the browser console reports the
error. This development SSO configuration requires access to the existing server;
no local Keycloak server or client secret is needed.
For logout, allow `http://localhost:4200/signed-out` under the client's
**Valid post logout redirect URIs**. The account banner shows the email claim
when Keycloak includes it; it does not display a username as an email.

The development proxy forwards `/api/*` to `http://127.0.0.1:8000`, removing
the `/api` prefix. Restart the backend after updating so the current API routes
and additive database migrations are loaded. No CORS changes are required.
If your backend uses a different address, change `frontend/proxy.conf.json` and restart
Angular. The API-documentation link targets the default backend address.

## Scope

- Shared inventory uses connector-neutral terminology. Device name displays the
  existing `endpoint_name` and Onboarding time (UTC) shows the stored
  `first_registered_at`. The list does not show Last seen or poll the connector.
  The device overview shows Last seen (UTC) in its details grid and fetches it
  for the selected device only.
  It shows the connector's last recorded contact, not live online/offline status.
- The header shows the signed-in email and a Log out button. It also shows a
  Super Admin banner for the concept demo.
  The label is not derived from Keycloak and does not grant or enforce roles.
  Keycloak login gates frontend startup, but the backend does not validate tokens
  or enforce authorization; it remains accessible directly.
- The desktop sidebar collapses to an icon rail and expands using the header
  toggle. Icons retain accessible names and tooltips. Mobile keeps its separate
  navigation drawer; collapse state lasts only for the current page session.
- Expanded sidebar branding is a compact O icon with One, aligned top-left
  and vertically centered. The collapsed rail keeps the O icon alone.
- Dashboard shows Devices and Recently active. The activity estimate checks
  linked devices for contact in the last 120 seconds every 30 seconds while
  the dashboard is open. It does not sync inventory or verify live connectivity.
- Search by device/friendly name, creation-time sorting and pagination are
  client-side. Devices default to newest-created-first; the Created time (UTC)
  arrow switches to oldest-first. Equal timestamps use ID ordering; missing
  creation timestamps stay last in both directions. No provider request is made.
- Choose a connector before creating a device. AVSystem Coiote is currently the
  only available connector; selecting it reveals security mode and PSK fields.
  Endpoint and friendly name are shared fields. Deselecting the connector clears
  secrets and resets security mode to PSK. Create supports PSK or no-security
  test mode.
- Click an endpoint to open `/devices/{endpoint}`. Overview shows stored
  administrative details, registration timestamps and a filtered Coiote snapshot.
  Settings & configuration opens a separate view for LwM2M server and Device
  resources.
- Registration lifetime and eight allowlisted communication settings are under a
  collapsible LwM2M server section. Lifetime can be read and written, with fresh
  read-back verification for writes. Every lifetime read fetches a fresh value
  from the device through Coiote, including the **Sync Current Lifetime** button,
  opening Settings and reloading details in that view.
  See [DEVICE_LIFETIME.md](DEVICE_LIFETIME.md) for timeouts and safety.
- The server section includes notification storing, binding, initial registration
  delay, communication retry count/timer, sequence delay/retry count and Mute Send.
  Settings are available according to discovered device capabilities; changes to
  communication settings can interrupt connectivity. Successful writes require a
  matching fresh read-back, not just task acceptance.
- The Device section shows supported observations and can request fresh readings.
  Reboot requires confirmation and device support; command success does not
  confirm the device is back online. Test it only on a safe device.
- **Coiote connector → Configure** saves salted PBKDF2-SHA256 hashes in local
  SQLite through a same-origin loopback request. This prototype does not verify
  a Coiote login or change the credentials used by the active connector.
  Configure is left of Check connection. A successful save shows a rounded green
  **Configured Successfully** toast with a white checkmark below Configure for 12 seconds.
  This feedback is not persisted and clears on a page refresh.
- Rename the friendly name only.
- Delete with an exact endpoint-name confirmation. This can delete the remote
  Coiote record too; it does not stop the running device client.
- Inventory loads automatically from the platform database, without a Reload
  list button. Reload details also refreshes Last seen for the selected device.
  No separate sync UI action.
- No group operations in the frontend, arbitrary device writes,
  CMP functionality, or direct access to Coiote credentials.

| Frontend request                 | Existing backend route       |
| -------------------------------- | ---------------------------- |
| `GET /api/health`                | `GET /health`                |
| `GET /api/device-activity`       | `GET /device-activity`       |
| `PUT /api/connectors/coiote/configuration` | `PUT /connectors/coiote/configuration` |
| `GET /api/devices`               | `GET /devices`               |
| `POST /api/devices`              | `POST /devices`              |
| `GET /api/devices/{endpoint}`    | `GET /devices/{endpoint}`    |
| `GET /api/devices/{endpoint}/last-seen` | `GET /devices/{endpoint}/last-seen` |
| `PATCH /api/devices/{endpoint}`  | `PATCH /devices/{endpoint}`  |
| `DELETE /api/devices/{endpoint}` | `DELETE /devices/{endpoint}` |
| `POST /api/devices/{endpoint}/lifetime/read` | `POST /devices/{endpoint}/lifetime/read` |
| `PATCH /api/devices/{endpoint}/lifetime` | `PATCH /devices/{endpoint}/lifetime` |
| `GET /api/devices/{endpoint}/server-settings` | `GET /devices/{endpoint}/server-settings` |
| `POST /api/devices/{endpoint}/server-settings/{resourceId}/read` | `POST /devices/{endpoint}/server-settings/{resourceId}/read` |
| `PATCH /api/devices/{endpoint}/server-settings/{resourceId}` | `PATCH /devices/{endpoint}/server-settings/{resourceId}` |
| `GET /api/devices/{endpoint}/server-settings/operations/{id}` | `GET /devices/{endpoint}/server-settings/operations/{id}` |
| `GET /api/devices/{endpoint}/device-settings` | `GET /devices/{endpoint}/device-settings` |
| `POST /api/devices/{endpoint}/device-settings/{resourceId}/read` | `POST /devices/{endpoint}/device-settings/{resourceId}/read` |
| `POST /api/devices/{endpoint}/device-settings/reboot` | `POST /devices/{endpoint}/device-settings/reboot` |

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
- Device-page loading fetches local administrative details. Device resource
  discovery and direct resource Read/Write controls are not exposed in the UI.
- Server-setting operations are persisted and recovered on page load. Pending
  results are checked automatically at two-second intervals, up to 30 checks,
  with manual checks available afterward. Checks do not resubmit a command.
  Only one of the eight server-setting operations may be unresolved per device.
  A submission without a confirmed task ID remains blocked and requires
  investigation; do not blindly retry after an unknown outcome.
- Firmware upload and status endpoints exist but firmware submissions are
  disabled unless `FOTA_ENABLED=true`; they have no frontend UI.
  An enabled firmware upload requires a known stored connector domain; refresh
  the device metadata if it is missing rather than assuming an account domain.
  Firmware operations require separate review, device compatibility and safe
  recovery planning. The current backend is not authorized for public exposure.
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
