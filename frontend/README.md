# Kore One DM Prototype

Angular frontend for FastAPI device CRUD, inventory and device communication APIs.
The layout follows the supplied KORE Wireless console references: a light-gray
sidebar, blue outlined actions, bright-blue table links, thin grid borders,
compact badges and structured details. The logo and favicon use the orange/red
O extracted from the user-supplied KORE image, alongside locally bundled Lucide
SVG icons. Devices is the initial view; dashboard
counts are separate. Promotional panels and repeated explanatory text are omitted.
Subtle droplet/ripple transitions respect `prefers-reduced-motion`.

## Run locally

Use **Linux Node.js 22.22.3 or newer in the 22.x line**, or another version supported
by Angular 22. Check `node --version` and `npm --version` inside WSL. Do not use the
Windows npm executable to install dependencies in a WSL filesystem.

Start the existing backend in one terminal, from the repository root:

```bash
venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm start
```

Open **http://localhost:4200**.

The development proxy forwards `/api/*` to `http://127.0.0.1:8000`, removing
the `/api` prefix. Restart the backend after pulling changes so the approved
timezone Write route and additive database migration are loaded. No CORS changes are required.
If your backend uses a different address, change `proxy.conf.json` and restart
Angular. The API-documentation link targets the default backend address.

## Scope

- Shared inventory uses connector-neutral terminology. Device name displays the
  existing `endpoint_name`; API fields and links are unchanged. The old onboarding
  state badge is removed: Onboarding time (UTC) shows `first_registered_at`, and
  Status shows `last_seen_at`, explicitly labelled Last seen (UTC). Missing
  registration is shown as a dash; missing contact as No contact recorded.
  These are stored timestamps, not verified live online/offline states.
  Provider names/settings remain in connector-specific areas.
- The header shows a Super Admin banner below Kore One DM Prototype for the concept demo. This is
  a presentation label only, not implemented authentication, authorization or
  role-based access control.
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
- Read the device timezone before editing it. Apply to device submits the
  approved timezone-only Write followed by Read-back verification. The UI shows
  Applied and verified only after the backend confirms a matching returned value.
- Lifetime (seconds) is an editable, page-local draft in Settings & configuration.
  It appears above Timezone in a compact, width-limited layout without large
  surrounding card borders.
  Its Apply action is disabled; it is not read, persisted or sent to the device.
  Discard clears it, as does opening another device. The info icon beside
  Registration lifetime explains its purpose and links to AVSystem's
  [Anjay Server Object documentation](https://docs.avsystem.com/hubfs/Anjay_Docs/BasicClient/BC-MandatoryObjects.html#server-object).
  Lifetime controls registration validity, not sensor-reporting frequency or a
  verified online status. The manufacturer/model/firmware Attributes section has
  been removed from the UI; backend resource and Read APIs are unchanged.
- Rename the friendly name only.
- Delete with an exact endpoint-name confirmation. This can delete the remote
  Coiote record too; it does not stop the running device client.
- Reload list/details from the platform database through existing device GETs.
  These requests make no Coiote calls. No separate sync UI action.
- No group operations, arbitrary writes/commands, login, CMP functionality,
  automatic polling, or direct access to Coiote credentials.

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

## Important behavior

- Connector selection is a frontend provider choice, not the backend's
  `connector_type` protocol metadata. It sends the unchanged device-create
  payload to the existing Coiote-backed API; no other provider or dispatch route
  is introduced. Coiote account credentials remain configured on the backend.
- List/detail GETs read SQLite only; they do not rediscover devices, import
  provider-only records or fetch provider metadata. Creation already stores
  the returned Coiote ID, so a newly created device is immediately readable.
  Existing local/imported records are retained. The UI does not poll.
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
- Pending or unconfirmed operations block another submission for that resource
  in the current page. Check result is manual; no background polling or automatic
  retries. Result-fetch failures retain the unresolved state. An uncertain
  submission without an operation ID cannot be safely retried from that page.
- Operations are persisted on the backend, but page operation state is not
  restored after navigation/reload. The backend rejects a known unresolved
  timezone Write with its operation ID, allowing manual checking. This guard
  covers sequential requests, not atomic concurrent submission. Do not blindly
  resubmit after an unknown outcome.
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

```bash
npm run build
npm test -- --watch=false
```

Tests cover existing API methods/payloads, backend reconnection, search/pagination, credential validation
and clearing, uncertain submissions, delete confirmation, failure handling,
details, routing, manual operation checks, Read-before-Write, verified settings,
uncertainty without retries, stale-response isolation, and inventory refresh.
They use mocks and do not contact Coiote.

The production build is in `dist/nexyn-console/browser`. The development proxy
is **not** part of a production build: a deployment needs a same-origin reverse
proxy that strips `/api` and forwards it to FastAPI, plus a SPA fallback to
`index.html` for frontend device deep links. No deployment configuration is introduced here.

Icons are from `lucide-static`, distributed under the ISC license. Selected SVGs
and the required license are bundled in `public/icons`. No external icon CDN,
font service, or analytics is used.
