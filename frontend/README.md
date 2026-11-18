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
the `/api` prefix. Restart the backend after pulling changes so the lifetime API
and additive database migration are loaded. No CORS changes are required.
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
- Click an endpoint to open Overview at `/devices/{endpoint}`. Choose
  Settings & configuration to open a separate view at `/devices/{endpoint}?view=settings`.
  Only the selected view is displayed. Overview shows stored administrative details, registration
  timestamps and a filtered stored Coiote snapshot, not live resource values.
- The Timezone section is not shown in this frontend. Backend resource,
  timezone Read and timezone Write APIs are unchanged.
- **LwM2M server** appears in the left **Object** table. The middle **Resource**
  table lists all nine resources by name. Use the eye to view a resource's value,
  timestamp and help in the **Details** pane, or the pencil to open its editor.
  Read-only resources show a lock instead of a pencil, with the tooltip
  “This resource is read-only.”
  Switching settings or collapsing the group preserves drafts and pending checks.
- **Device** lists Manufacturer, Model Number, Serial Number, Firmware Version,
  Available Power Sources (one row per reported instance), and Reboot.
  The five readable resources show cached observations and can request a fresh
  read. Each Device setting shows a short description and a help icon with more
  detail, matching LwM2M server settings. Reboot requires explicit confirmation,
  may interrupt communication, and a successful task is not proof the device is back online. Factory Reset is
  not exposed. Device reads and Reboot reuse the Server settings task lock and
  result checks.
- Registration lifetime loads automatically when opening or refreshing Settings
  & configuration. Show current lifetime can refresh it on demand, and Apply to
  device changes it. The display is the latest confirmed backend value and its
  observation time; the backend contacts the device only when it has no stored
  confirmed value. Writes are shown as applied only after the backend verifies
  the device read-back. If a write outcome is unknown, the UI blocks resubmission.
- Lifetime (seconds) is an editable, page-local draft in Settings & configuration.
  It appears in a compact, width-limited layout without large
  surrounding card borders.
  Discard clears the draft, as does opening another device. The info icon beside
  Registration lifetime explains its purpose and links to AVSystem's
  [Anjay Server Object documentation](https://docs.avsystem.com/hubfs/Anjay_Docs/BasicClient/BC-MandatoryObjects.html#server-object).
  Lifetime controls registration validity, not sensor-reporting frequency or a
  verified online status. The manufacturer/model/firmware Attributes section has
  been removed from the UI; backend resource and Read APIs are unchanged.
- Each LwM2M server setting has a help button that explains what it controls,
  what changing it affects, and where to find the protocol reference.
- The same LwM2M server section includes the other eight editable fields from the
  supplied Server-object view: Notification storing (/6), Binding (/7), Initial
  registration delay timer (/14), Communication retry count (/17), Communication
  retry timer (/18), Communication sequence delay timer (/19), Communication
  sequence retry count (/20), and Mute Send (/23). Lifetime (/1) keeps its existing
  API and controls within this section. No Disable, Bootstrap Request, or other executable command
  is exposed.
- Server settings load automatically under LwM2M server when opening the settings view;
  there is no separate Load/Reload server settings button. The backend discovers
  the configured server instance and each
  resource's actual Read/Write permissions through the backend. Absent or
  non-writable fields are unavailable; they are not assumed writable from a
  screenshot. Last observed values include their timestamps and may be stale.
  Read from device schedules a fresh Read. Boolean selectors preserve true/false,
  numeric fields accept whole numbers from 0 to 4294967295, and Binding accepts
  a device-supported transport code (for example U). The backend enforces types,
  ranges and the resource allowlist again.
- Applying a server communication setting requires a valid value, with no
  acknowledgement checkbox. Per-setting guidance remains visible. The backend
  persists the operation before submission and schedules
  Write then Read. Pending operations are checked automatically at two-second
  intervals (up to 30 checks per submission, recovery, or manual check), stopping
  on completion, errors, navigation, or page destruction. Checks only retrieve
  results; they never resubmit a device write or read. Check result remains
  available after automatic checks pause. Only a successful
  task and fresh matching read-back display Applied and verified. Failed,
  mismatched, pending and unconfirmed submissions are distinct states.
  Results show their read-back value and observation time; a successful write
  does not guarantee that the value cannot change again later.
  Only one of these eight setting operations can be unresolved per device.
  Opening or refreshing the settings view recovers and reconciles a persisted
  pending operation, returning a just-completed result and releasing its lock.
  The backend also reconciles an existing operation before rejecting a new
  submission as blocked. A completed write no longer stays pending until the user
  finds Check result. Pending delivery to a sleeping device can take minutes;
  pending is not failure, and additional operations remain blocked until resolved.
  A submission with no confirmed provider task ID stays blocked and requires
  connector-side investigation; reloading is not proof that the write failed.
  The existing lifetime API and its registration-update behavior are unchanged.
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
| `POST /api/devices/{endpoint}/lifetime/read` | `POST /devices/{endpoint}/lifetime/read` |
| `PATCH /api/devices/{endpoint}/lifetime` | `PATCH /devices/{endpoint}/lifetime` |
| `GET /api/devices/{endpoint}/server-settings` | `GET /devices/{endpoint}/server-settings` |
| `POST /api/devices/{endpoint}/server-settings/{resourceId}/read` | `POST /devices/{endpoint}/server-settings/{resourceId}/read` |
| `PATCH /api/devices/{endpoint}/server-settings/{resourceId}` | `PATCH /devices/{endpoint}/server-settings/{resourceId}` |
| `GET /api/devices/{endpoint}/server-settings/operations/{id}` | `GET /devices/{endpoint}/server-settings/operations/{id}` |
| `GET /api/devices/{endpoint}/device-settings` | `GET /devices/{endpoint}/device-settings` |
| `POST /api/devices/{endpoint}/device-settings/{resourceId}/read` | `POST /devices/{endpoint}/device-settings/{resourceId}/read` |
| `POST /api/devices/{endpoint}/device-settings/reboot` | `POST /devices/{endpoint}/device-settings/reboot` (requires `{ "confirm": true }`) |

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
- Device-page loading reads local administrative details. Opening settings
  discovers server settings and loads the latest confirmed lifetime.
  Switching views on the same device preserves drafts and pending result checks.
  It does not discover provider resources for timezone controls.
  Lifetime writes still require an explicit Apply to device action.
  Bounded result polling starts only
  for a known pending server-setting operation; no automatic device-command
  retries or continuous inventory/resource polling are performed by the frontend.
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
details, routing, registration-lifetime and server communication controls, verified settings,
uncertainty without retries, stale-response isolation, and inventory refresh.
They use mocks and do not contact Coiote.

The production build is in `dist/nexyn-console/browser`. The development proxy
is **not** part of a production build: a deployment needs a same-origin reverse
proxy that strips `/api` and forwards it to FastAPI, plus a SPA fallback to
`index.html` for frontend device deep links. No deployment configuration is introduced here.

Icons are from `lucide-static`, distributed under the ISC license. Selected SVGs
and the required license are bundled in `public/icons`. No external icon CDN,
font service, or analytics is used.
