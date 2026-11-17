# nexyn-backend
Connector based DMP 

## Device frontend

The [Angular frontend](frontend/README.md), titled Kore One DM Prototype, provides a device
workspace for the existing create/list/detail/rename/delete endpoints and stored-inventory reload.
List/details load directly from SQLite without Coiote requests. Reloading them
does not refresh stored registration/contact history; explicit provider
reconciliation remains available through the existing backend sync route only.
Each device also has a dedicated page for registration-lifetime reads and
verified writes, plus eight allowlisted server communication settings under a
collapsible LwM2M server heading. Operations have backend validation, persistence,
automatic result checks, and fresh read-back verification.
Timezone controls are not shown in the frontend.
See its README for prerequisites, local startup, tests, and deployment caveats.

From the repository root, start FastAPI:

```bash
venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

With a supported Linux Node.js installation, use a separate terminal:

```bash
cd frontend
npm ci
npm start
```

Open http://localhost:4200. The frontend development proxy connects to the backend
on port 8000. Restart FastAPI after updating to load the lifetime API and its
additive database migration.

This is a local proof of concept, not a production deployment. Use test
credentials only: API access control and secure PSK storage remain outstanding.
