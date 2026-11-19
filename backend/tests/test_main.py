import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.database import initialize_database
from backend.models import Device, DeviceOperation, Group


class DeviceAPITestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        main.Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

        def get_test_db():
            with self.session_factory() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_test_db
        self.client = TestClient(main.app)
        self.create_patch = patch("backend.main.create_coiote_device")
        self.create = self.create_patch.start()
        self.create.return_value = {"id": "test-endpoint"}
        self.payload = {
            "endpoint_name": "test-endpoint",
            "friendly_name": "Test device",
            "psk_identity": "test-identity",
            "psk_key": "00112233445566778899aabbccddeeff",
        }

    def tearDown(self):
        self.create_patch.stop()
        self.client.close()
        main.app.dependency_overrides.pop(main.get_db)
        self.engine.dispose()


class FirmwareUpdateAPITests(DeviceAPITestCase):
    def create_device_with_domain(self):
        self.client.post("/devices", json=self.payload)
        with self.session_factory() as db:
            device = db.query(Device).filter_by(endpoint_name="test-endpoint").one()
            device.domain = "/main/test-domain/"
            db.commit()

    def test_firmware_update_is_disabled_by_default(self):
        with patch("backend.main.FOTA_ENABLED", False):
            response = self.client.post(
                "/devices/firmware/test-endpoint",
                content=b"test-firmware",
                headers={"content-type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Firmware updates are disabled"})
        self.create.assert_not_called()

    def test_upload_and_update_returns_accepted_task(self):
        self.create_device_with_domain()
        with (
            patch("backend.main.FOTA_ENABLED", True),
            patch("backend.main.create_firmware_resource", new_callable=AsyncMock) as create_resource,
            patch("backend.main.upload_firmware_data", new_callable=AsyncMock) as upload,
            patch("backend.main.start_firmware_update", new_callable=AsyncMock) as start,
        ):
            create_resource.return_value = "resource-123"
            start.return_value = "task-123"

            async def consume_upload(resource_id, content):
                async for _ in content:
                    pass

            upload.side_effect = consume_upload
            response = self.client.post(
                "/devices/firmware/test-endpoint?filename=demo.pkg",
                content=b"firmware-bytes",
                headers={"content-type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"task_id": "task-123", "state": "accepted"})
        resource_name, filename, domain = create_resource.call_args.args
        self.assertTrue(resource_name.startswith("nexyn-firmware-"))
        self.assertEqual((filename, domain), ("demo.pkg", "/main/test-domain/"))
        upload.assert_awaited_once()
        start.assert_awaited_once_with("test-endpoint", "resource-123")

    def test_upload_rejects_bin_filename(self):
        with (
            patch("backend.main.FOTA_ENABLED", True),
            patch("backend.main.create_firmware_resource", new_callable=AsyncMock) as create_resource,
        ):
            response = self.client.post(
                "/devices/firmware/test-endpoint?filename=demo.bin",
                content=b"firmware-bytes",
                headers={"content-type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(), {"detail": "Firmware filename must use the .pkg extension"}
        )
        create_resource.assert_not_awaited()

    def test_upload_rejects_missing_domain_without_calling_connector(self):
        self.client.post("/devices", json=self.payload)
        with (
            patch("backend.main.FOTA_ENABLED", True),
            patch("backend.main.create_firmware_resource", new_callable=AsyncMock) as create_resource,
        ):
            response = self.client.post(
                "/devices/firmware/test-endpoint?filename=demo.pkg",
                content=b"firmware-bytes",
                headers={"content-type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 409)
        create_resource.assert_not_awaited()

    def test_resource_forbidden_error_identifies_fota_stage(self):
        self.create_device_with_domain()
        request = httpx.Request("POST", "https://coiote.invalid/resources")
        upstream_response = httpx.Response(403, request=request)
        error = httpx.HTTPStatusError(
            "forbidden", request=request, response=upstream_response
        )
        with (
            patch("backend.main.FOTA_ENABLED", True),
            patch("backend.main.create_firmware_resource", new_callable=AsyncMock) as create_resource,
        ):
            create_resource.side_effect = error
            response = self.client.post(
                "/devices/firmware/test-endpoint?filename=demo.pkg",
                content=b"firmware-bytes",
                headers={"content-type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Coiote firmware resource creation failed (HTTP 403)"},
        )

    def test_status_returns_only_allowlisted_firmware_fields(self):
        self.client.post("/devices", json=self.payload)
        report = {
            "status": "Success",
            "summary": "Firmware update successful",
            "startTime": "2026-11-12T10:00:00Z",
            "finishTime": "2026-11-12T10:01:00Z",
            "properties": [
                {"name": "newFirmwareVersion", "value": "2.0"},
                {"name": "result", "value": "SUCCESS"},
                {"name": "unexpectedSecret", "value": "must not leak"},
            ],
        }
        with patch(
            "backend.main.get_firmware_task_report", new_callable=AsyncMock
        ) as get_report:
            get_report.return_value = report
            response = self.client.get(
                "/devices/firmware/test-endpoint/task-123"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "succeeded")
        self.assertEqual(response.json()["firmware"], {
            "new_firmware_version": "2.0",
            "result": "SUCCESS",
        })
        self.assertNotIn("unexpectedSecret", response.text)

    def test_status_treats_report_code_4041_as_pending(self):
        self.client.post("/devices", json=self.payload)
        request = httpx.Request("GET", "https://coiote.invalid/taskReports/task/test-endpoint")
        upstream_response = httpx.Response(404, json={"code": 4041}, request=request)
        error = httpx.HTTPStatusError(
            "not ready", request=request, response=upstream_response
        )
        with patch(
            "backend.main.get_firmware_task_report", new_callable=AsyncMock
        ) as get_report:
            get_report.side_effect = error
            response = self.client.get(
                "/devices/firmware/test-endpoint/task-123"
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(), {"task_id": "task-123", "state": "pending"}
        )


class DeviceTimeRouteRemovalTests(DeviceAPITestCase):
    def test_device_time_route_is_not_registered(self):
        routes = {route.path for route in main.app.routes if hasattr(route, "path")}
        self.assertNotIn("/devices/{endpoint_name}/time", routes)


class DeviceResourceTests(DeviceAPITestCase):
    def setUp(self):
        super().setUp()
        self.remote = self.enterContext(patch("backend.main.get_coiote_device_resources"))
        self.remote.return_value = [
            {"path": "Device.0.Manufacturer", "resourceId": 0,
             "operations": ["Read"], "type": "String", "value": "stored manufacturer"},
            {"path": "Device.0.Model Number", "resourceId": 1,
             "operations": ["Read"], "type": "String"},
            {"path": "Device.0.Firmware Version", "resourceId": 3,
             "operations": ["Read"], "type": "String"},
            {"path": "Security.0.Secret Key", "resourceId": 5,
             "operations": ["Read", "Write"], "type": "Opaque", "value": "secret"},
        ]
        with self.session_factory() as db:
            db.add(Device(
                endpoint_name="test-endpoint", coiote_device_id="different-remote-id",
                friendly_name="Unchanged", psk_key="secret",
            ))
            db.commit()

    def test_discovery_filters_values_and_sensitive_resources_without_writes(self):
        with (
            patch("backend.device_sync.list_coiote_devices") as inventory,
            patch("backend.device_sync.get_coiote_device_details") as details,
        ):
            response = self.client.get("/devices/test-endpoint/resources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["source"], "coiote_stored_model")
        self.assertEqual(body["endpoint_name"], "test-endpoint")
        self.assertTrue(body["fetched_at"].endswith("Z"))
        self.assertEqual(len(body["resources"]), 3)
        for resource in body["resources"]:
            self.assertEqual(set(resource), {"key", "resource_id", "data_type", "operations"})
            self.assertEqual(resource["operations"], ["Read"])
        self.assertNotIn("stored manufacturer", response.text)
        self.assertNotIn("secret", response.text)
        self.assertNotIn("Security", response.text)
        self.remote.assert_called_once_with("different-remote-id")
        inventory.assert_not_called()
        details.assert_not_called()
        self.create.assert_not_called()
        with self.session_factory() as db:
            device = db.query(Device).one()
            self.assertEqual(device.friendly_name, "Unchanged")
            self.assertEqual(device.psk_key, "secret")
            self.assertIsNone(device.refreshed_at)

    def test_unknown_or_local_only_device_does_not_call_provider(self):
        self.assertEqual(
            self.client.get("/devices/unknown/resources").status_code, 404
        )
        with self.session_factory() as db:
            db.query(Device).one().coiote_device_id = None
            db.commit()
        response = self.client.get("/devices/test-endpoint/resources")
        self.assertEqual(response.status_code, 409)
        self.remote.assert_not_called()

    def test_empty_model_is_explicit_empty_discovery_not_online_proof(self):
        self.remote.return_value = []
        response = self.client.get("/devices/test-endpoint/resources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resources"], [])
        self.assertNotIn("connection_status", response.json())

    def test_provider_errors_are_sanitized_without_fallback(self):
        request = httpx.Request("GET", "https://example.invalid/resources")
        errors = [
            httpx.ConnectError("secret"),
            httpx.ReadTimeout("secret"),
        ] + [
            httpx.HTTPStatusError(
                "secret", request=request,
                response=httpx.Response(status, request=request, text="secret"),
            ) for status in (401, 403, 404, 429, 500)
        ]
        for error in errors:
            with self.subTest(error=error):
                self.remote.side_effect = error
                response = self.client.get("/devices/test-endpoint/resources")
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("secret", response.text)

    def test_malformed_and_conflicting_models_fail_explicitly(self):
        record = self.remote.return_value[0]
        payloads = [
            None, {}, ["secret"], [{}],
            [{**record, "resourceId": "0"}],
            [{**record, "operations": ["Invented"]}],
            [{**record, "type": "Invented"}],
            [{**record, "resourceId": 1}],
            [record, record],
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.remote.return_value = payload
                response = self.client.get("/devices/test-endpoint/resources")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.json()["detail"], "Coiote returned an invalid resource response"
                )
        self.remote.side_effect = ValueError("secret invalid JSON")
        self.assertEqual(
            self.client.get("/devices/test-endpoint/resources").status_code, 502
        )


class DeviceReadTests(DeviceAPITestCase):
    def setUp(self):
        super().setUp()
        self.resources = self.enterContext(patch("backend.main.get_coiote_device_resources"))
        self.resources.return_value = [{
            "path": "Device.0.Manufacturer", "resourceId": 0,
            "type": "String", "operations": ["Read"],
        }]
        self.submit = self.enterContext(patch("backend.main.submit_coiote_read", return_value="task:1"))
        self.report = self.enterContext(patch("backend.main.get_coiote_task_report"))
        self.report.return_value = {
            "taskId": "task:1", "deviceId": "remote/id",
            "status": "Success", "finishTime": "2026-11-11T10:00:00Z",
            "properties": [
                {"name": "Device.0.Manufacturer", "value": "Test manufacturer"},
                {"name": "internal", "value": "secret upstream diagnostic"},
            ],
        }
        with self.session_factory() as db:
            db.add(Device(endpoint_name="read-device", coiote_device_id="remote/id"))
            db.add(Device(endpoint_name="other-device", coiote_device_id="other"))
            db.commit()
        self.url = "/devices/read-device"
        self.body = {"resource_key": "Device.0.Manufacturer"}

    def submit_read(self):
        response = self.client.post(f"{self.url}/reads", json=self.body)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "pending")
        self.assertIsNone(response.json()["value"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.headers["location"]

    def test_submit_then_success_persists_result_without_replaying(self):
        location = self.submit_read()
        with self.session_factory() as db:
            operation = db.query(DeviceOperation).one()
            self.assertEqual(operation.task_id, "task:1")
            self.submit.assert_called_once_with(
                "remote/id", "Device.0.Manufacturer", operation.id
            )
        response = self.client.get(location)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["value"], "Test manufacturer")
        self.assertTrue(body["completed_at"].endswith("Z"))
        self.assertNotIn("secret", response.text)
        self.assertNotIn("task_id", body)
        self.assertEqual(self.client.get(location).json(), body)
        self.report.assert_called_once_with("task:1", "remote/id")
        self.submit.assert_called_once()

    def test_validation_and_capability_checks_prevent_submission(self):
        for key in ("Security.0.Secret Key", "Device.0.Reboot", ""):
            self.assertEqual(
                self.client.post(f"{self.url}/reads", json={"resource_key": key}).status_code,
                422,
            )
        self.resources.return_value[0]["operations"] = []
        self.assertEqual(
            self.client.post(f"{self.url}/reads", json=self.body).status_code, 422
        )
        self.submit.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(DeviceOperation).count(), 0)
        self.assertEqual(
            self.client.post("/devices/missing/reads", json=self.body).status_code, 404
        )
        with self.session_factory() as db:
            db.query(Device).filter_by(endpoint_name="read-device").one().coiote_device_id = None
            db.commit()
        self.assertEqual(
            self.client.post(f"{self.url}/reads", json=self.body).status_code, 409
        )

    def test_pending_codes_and_statuses_preserve_operation(self):
        location = self.submit_read()
        for status in ("NotStarted", "InProgress", "ErrorRetryable"):
            self.report.return_value["status"] = status
            self.assertEqual(self.client.get(location).json()["status"], "pending")
        request = httpx.Request("GET", "https://example.invalid/report")
        self.report.side_effect = httpx.HTTPStatusError(
            "secret", request=request,
            response=httpx.Response(404, request=request, json={"code": 4041}),
        )
        self.assertEqual(self.client.get(location).json()["status"], "pending")

    def test_failures_and_task_not_found_are_explicit(self):
        for status in ("Warning", "Error"):
            with self.subTest(status=status):
                location = self.submit_read()
                self.report.return_value["status"] = status
                body = self.client.get(location).json()
                self.assertEqual(body["status"], "failed")
                self.assertIsNone(body["value"])
                self.assertNotIn("secret", str(body))
        location = self.submit_read()
        request = httpx.Request("GET", "https://example.invalid/report")
        self.report.side_effect = httpx.HTTPStatusError(
            "secret", request=request,
            response=httpx.Response(404, request=request, json={"code": 4040}),
        )
        self.assertEqual(self.client.get(location).json()["status"], "not_found")

    def test_invalid_or_unavailable_reports_do_not_change_pending_state(self):
        location = self.submit_read()
        original = self.report.return_value
        reports = [
            {}, {**original, "deviceId": "other"},
            {**original, "taskId": "wrong"}, {**original, "status": "invented"},
            {**original, "properties": []},
            {**original, "properties": original["properties"] * 2},
            {**original, "finishTime": None},
            {**original, "finishTime": "2026-11-11T10:00:00"},
        ]
        for report in reports:
            self.report.return_value = report
            self.assertEqual(self.client.get(location).status_code, 502)
        self.report.side_effect = httpx.ReadTimeout("secret")
        response = self.client.get(location)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)
        with self.session_factory() as db:
            self.assertEqual(db.query(DeviceOperation).one().status, "pending")

    def test_submission_uncertainty_is_saved_and_not_retried(self):
        for error in (httpx.ReadTimeout("secret"), ValueError("secret invalid JSON")):
            self.submit.side_effect = error
            response = self.client.post(f"{self.url}/reads", json=self.body)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("secret", response.text)
            operation_id = response.json()["detail"]["operation_id"]
            body = self.client.get(f"{self.url}/operations/{operation_id}").json()
            self.assertEqual(body["status"], "submission_unknown")
        self.assertEqual(self.submit.call_count, 2)
        self.report.assert_not_called()

    def test_rejected_and_invalid_submission_response(self):
        request = httpx.Request("POST", "https://example.invalid/tasks")
        self.submit.side_effect = httpx.HTTPStatusError(
            "secret", request=request,
            response=httpx.Response(403, request=request),
        )
        response = self.client.post(f"{self.url}/reads", json=self.body)
        operation_id = response.json()["detail"]["operation_id"]
        self.assertEqual(
            self.client.get(f"{self.url}/operations/{operation_id}").json()["status"],
            "rejected",
        )
        self.submit.side_effect = None
        self.submit.return_value = {"id": "not-a-task-id-string"}
        self.assertEqual(
            self.client.post(f"{self.url}/reads", json=self.body).status_code, 502
        )

    def test_operation_cannot_be_fetched_for_another_device(self):
        location = self.submit_read()
        self.assertEqual(
            self.client.get(location.replace("/read-device/", "/other-device/")).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"{self.url}/operations/missing").status_code, 404
        )
        self.report.assert_not_called()

    def test_upstream_http_report_errors_do_not_become_task_failures(self):
        location = self.submit_read()
        request = httpx.Request("GET", "https://example.invalid/report")
        for status, payload in ((401, {}), (403, {}), (429, {}), (500, {}),
                                (404, {"code": 9999}), (404, [])):
            self.report.side_effect = httpx.HTTPStatusError(
                "secret", request=request,
                response=httpx.Response(status, request=request, json=payload),
            )
            response = self.client.get(location)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("secret", response.text)
        with self.session_factory() as db:
            self.assertEqual(db.query(DeviceOperation).one().status, "pending")

    def test_initialization_preserves_submitted_operations(self):
        self.submit_read()
        initialize_database(self.engine)
        initialize_database(self.engine)
        with self.session_factory() as db:
            self.assertEqual(db.query(DeviceOperation).one().task_id, "task:1")
            self.assertEqual(db.query(Device).count(), 2)


class DeviceWriteTests(DeviceAPITestCase):
    def setUp(self):
        super().setUp()
        self.resources = self.enterContext(patch("backend.main.get_coiote_device_resources"))
        self.resources.return_value = [{
            "path": "Device.0.Timezone", "resourceId": 15,
            "type": "String", "operations": ["Read", "Write"],
        }]
        self.submit = self.enterContext(patch(
            "backend.main.submit_coiote_timezone_write", return_value="task:tz"
        ))
        self.report = self.enterContext(patch("backend.main.get_coiote_task_report"))
        self.report.return_value = {
            "taskId": "task:tz", "deviceId": "remote/id", "status": "Success",
            "finishTime": "2026-11-16T07:00:00Z",
            "properties": [{"name": "Device.0.Timezone", "value": "Etc/UTC"}],
        }
        with self.session_factory() as db:
            db.add(Device(endpoint_name="write-device", coiote_device_id="remote/id"))
            db.commit()
        self.url = "/devices/write-device/writes"
        self.body = {"resource_key": "Device.0.Timezone", "value": "Etc/UTC"}

    def test_write_is_pending_until_read_back_matches_and_result_is_persistent(self):
        response = self.client.post(self.url, json={**self.body, "value": " Etc/UTC "})
        self.assertEqual(response.status_code, 202, response.text)
        operation = response.json()
        self.assertEqual(operation["kind"], "write")
        self.assertEqual(operation["requested_value"], "Etc/UTC")
        self.assertEqual(operation["status"], "pending")
        self.assertIsNone(operation["value"])
        self.submit.assert_called_once_with(
            "remote/id", "Device.0.Timezone", "Etc/UTC", operation["id"]
        )
        result = self.client.get(response.headers["location"])
        self.assertEqual(result.json()["status"], "succeeded")
        self.assertEqual(result.json()["value"], "Etc/UTC")
        self.assertEqual(self.client.get(response.headers["location"]).json(), result.json())
        self.report.assert_called_once()

    def test_only_valid_readable_writable_timezone_can_be_submitted(self):
        for value in ("", "../Etc/UTC", "Not/AZone", "A" * 32):
            with self.subTest(value=value):
                self.assertEqual(self.client.post(
                    self.url, json={**self.body, "value": value}
                ).status_code, 422)
        for key in ("Device.0.Manufacturer", "Device.1.Timezone", "Security.0.Secret Key"):
            self.assertEqual(self.client.post(
                self.url, json={**self.body, "resource_key": key}
            ).status_code, 422)
        self.resources.assert_not_called()
        self.resources.return_value[0]["operations"] = ["Read"]
        self.assertEqual(self.client.post(self.url, json=self.body).status_code, 422)
        self.resources.return_value[0]["type"] = "Integer"
        self.assertEqual(self.client.post(self.url, json=self.body).status_code, 502)
        self.submit.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(DeviceOperation).count(), 0)

    def test_successful_task_with_mismatched_read_back_is_not_synced(self):
        response = self.client.post(self.url, json=self.body)
        self.report.return_value["properties"][0]["value"] = "Europe/Warsaw"
        result = self.client.get(response.headers["location"]).json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["value"], "Europe/Warsaw")
        self.assertIn("did not match", result["error"])

    def test_unresolved_write_blocks_resubmission_and_uncertainty_is_explicit(self):
        self.submit.side_effect = httpx.ReadTimeout("secret")
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 502)
        operation_id = response.json()["detail"]["operation_id"]
        result = self.client.get(f"/devices/write-device/operations/{operation_id}").json()
        self.assertEqual(result["status"], "submission_unknown")
        self.assertNotIn("secret", response.text)
        repeat = self.client.post(self.url, json=self.body)
        self.assertEqual(repeat.status_code, 409)
        self.assertEqual(repeat.json()["detail"]["operation_id"], operation_id)
        self.submit.assert_called_once()
        self.report.assert_not_called()

    def test_legacy_read_operations_survive_additive_migration(self):
        engine = create_engine("sqlite://", poolclass=StaticPool)
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE device_operations (
                        id VARCHAR PRIMARY KEY, device_id INTEGER, coiote_device_id VARCHAR,
                        resource_key VARCHAR, task_id VARCHAR, status VARCHAR, value VARCHAR,
                        error VARCHAR, created_at DATETIME, completed_at DATETIME
                    )
                """))
                connection.execute(text("""
                    INSERT INTO device_operations
                    (id, device_id, coiote_device_id, resource_key, task_id, status, created_at)
                    VALUES ('old-read', 1, 'remote', 'Device.0.Manufacturer', 'task-old',
                            'pending', '2026-11-16 07:00:00')
                """))
            initialize_database(engine)
            initialize_database(engine)
            with engine.connect() as connection:
                row = connection.execute(text(
                    "SELECT kind, requested_value, task_id FROM device_operations"
                )).one()
                self.assertEqual(tuple(row), ("read", None, "task-old"))
        finally:
            engine.dispose()


class GroupAPITests(DeviceAPITestCase):
    def test_create_and_list_groups_are_local_and_persistent(self):
        with self.session_factory() as db:
            db.add(Device(endpoint_name="existing-device"))
            db.commit()
        with (
            patch("backend.device_sync.list_coiote_devices") as remote_list,
            patch("backend.device_sync.get_coiote_device_details") as remote_detail,
            patch("backend.main.update_coiote_device") as remote_update,
            patch("backend.main.delete_coiote_device") as remote_delete,
        ):
            self.assertEqual(self.client.get("/groups").json(), [])
            first = self.client.post("/groups", json={"name": "  Building A Sensors  "})
            self.assertEqual(first.status_code, 201)
            self.assertEqual(set(first.json()), {"id", "name"})
            self.assertIsInstance(first.json()["id"], int)
            self.assertEqual(first.json()["name"], "Building A Sensors")
            second = self.client.post("/groups", json={"name": "Building B Sensors"})
            self.assertEqual(second.status_code, 201)
            listed = self.client.get("/groups")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json(), [first.json(), second.json()])
            for remote in (self.create, remote_list, remote_detail,
                           remote_update, remote_delete):
                remote.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Group).count(), 2)
            self.assertEqual(db.get(Group, first.json()["id"]).name, "Building A Sensors")
            self.assertEqual(db.query(Device).one().endpoint_name, "existing-device")

    def test_invalid_group_names_are_rejected_without_writes(self):
        for payload in ({}, {"name": None}, {"name": ""}, {"name": " \t\n"},
                        {"name": 123}, {"name": []}):
            with self.subTest(payload=payload):
                response = self.client.post("/groups", json=payload)
                self.assertEqual(response.status_code, 422)
                for error in response.json()["detail"]:
                    self.assertEqual(set(error), {"type", "loc", "msg"})
        with self.session_factory() as db:
            self.assertEqual(db.query(Group).count(), 0)
        self.create.assert_not_called()

    def test_duplicate_names_conflict_but_capitalization_matters(self):
        self.assertEqual(
            self.client.post("/groups", json={"name": "Sensors"}).status_code, 201
        )
        for name in ("Sensors", "  Sensors  "):
            response = self.client.post("/groups", json={"name": name})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"detail": "Group name already exists"})
        self.assertEqual(
            self.client.post("/groups", json={"name": "sensors"}).status_code, 201
        )
        self.assertEqual(
            [group["name"] for group in self.client.get("/groups").json()],
            ["Sensors", "sensors"],
        )
        self.create.assert_not_called()


class DeviceCreateValidationTests(DeviceAPITestCase):
    def test_invalid_psk_is_rejected_before_writes_or_coiote_calls(self):
        invalid = {
            "psk_key": [None, "", "abc", "0x1234", "12 34", "1234\n",
                        "zz", "\uff11\uff12", "\u79d8\u5bc6", 1234],
            "psk_identity": [None, "", " \t\n", 1234],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = self.client.post(
                        "/devices", json={**self.payload, field: value}
                    )
                    self.assertEqual(response.status_code, 422)
                    errors = response.json()["detail"]
                    self.assertTrue(
                        any(item["loc"] == ["body", field] for item in errors)
                    )
                    for item in errors:
                        self.assertEqual(set(item), {"type", "loc", "msg"})
        for field in invalid:
            with self.subTest(omitted=field):
                payload = self.payload.copy()
                del payload[field]
                self.assertEqual(
                    self.client.post("/devices", json=payload).status_code, 422
                )
        self.create.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 0)

    def test_hex_keys_and_identity_are_preserved(self):
        for index, key in enumerate(("ab", "AABB", self.payload["psk_key"])):
            with self.subTest(key=key):
                endpoint = f"test-endpoint-{index}"
                self.create.return_value = {"id": endpoint}
                response = self.client.post(
                    "/devices",
                    json={**self.payload, "endpoint_name": f" {endpoint} ",
                          "psk_key": key},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["endpoint_name"], endpoint)
                self.assertEqual(response.json()["onboarding_status"], "coiote_record_created")
                self.assertNotIn("psk_key", response.json())
                self.assertNotIn("psk_identity", response.json())
                self.create.assert_called_with(
                    endpoint_name=endpoint,
                    friendly_name="Test device",
                    security_mode="psk",
                    psk_identity="test-identity",
                    psk_key=key,
                )
                with self.session_factory() as db:
                    device = db.query(Device).filter_by(endpoint_name=endpoint).one()
                    self.assertEqual(device.psk_key, key)

    def test_blank_endpoint_still_rejected(self):
        for endpoint in ("", " \t\n"):
            response = self.client.post(
                "/devices", json={**self.payload, "endpoint_name": endpoint}
            )
            self.assertEqual(response.status_code, 422)
        self.create.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 0)

    def test_non_psk_mode_keeps_existing_behavior(self):
        response = self.client.post(
            "/devices", json={"endpoint_name": "test-endpoint", "security_mode": "nosec"}
        )
        self.assertEqual(response.status_code, 200)
        self.create.assert_called_once_with(
            endpoint_name="test-endpoint", friendly_name=None,
            security_mode="nosec", psk_identity=None, psk_key=None,
        )

    def test_remote_failure_still_keeps_failed_local_record(self):
        request = httpx.Request("POST", "https://example.invalid/devices")
        self.create.side_effect = httpx.HTTPStatusError(
            "upstream secret", request=request,
            response=httpx.Response(400, request=request, text="upstream secret"),
        )
        response = self.client.post("/devices", json=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("upstream secret", response.text)
        with self.session_factory() as db:
            self.assertEqual(
                db.query(Device).one().onboarding_status, "coiote_registration_failed"
            )


class DeviceRefreshTests(DeviceAPITestCase):
    def test_inventory_sync_timestamp_records_empty_successes_but_not_failures(self):
        url = "/inventory-sync"
        initial = self.client.get(url)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers["cache-control"], "no-store")
        self.assertEqual(initial.json(), {"last_successful_sync_at": None})

        self.list_devices.return_value = []
        for day in (18, 19):
            now = datetime(2026, 11, day, 9, 0, tzinfo=timezone.utc)
            with patch("backend.device_sync.datetime") as clock:
                clock.now.return_value = now
                self.assertEqual(self.client.post("/sync-devices").status_code, 200)
            self.assertEqual(self.client.get(url).json(), {
                "last_successful_sync_at": f"2026-11-{day}T09:00:00Z",
            })

        self.list_devices.return_value = None
        self.assertEqual(self.client.post("/sync-devices").status_code, 502)
        self.assertEqual(self.client.get(url).json(), {
            "last_successful_sync_at": "2026-11-19T09:00:00Z",
        })
        self.assertEqual(self.client.get("/devices").json(), [])

    def setUp(self):
        super().setUp()
        self.list_patch = patch("backend.device_sync.list_coiote_devices")
        self.details_patch = patch("backend.device_sync.get_coiote_device_details")
        self.list_devices = self.list_patch.start()
        self.details = self.details_patch.start()
        self.list_devices.return_value = ["remote-id"]
        self.record = {
            "id": "remote-id",
            "properties": {"endpointName": "test-endpoint"},
            "friendlyName": "Current remote name",
            "securityMode": "psk",
            "connectorType": "management",
            "domain": "/test/",
            "firstRegisterTime": "2026-11-09T10:00:00Z",
            "lastRegisterTime": "2026-11-09T10:01:00Z",
            "lastContactTime": "2026-11-09T10:02:00Z",
            "dtlsIdentity": "upstream identity",
            "dtlsPsk": {"HexadecimalPsk": "upstream secret"},
            "unexpected": "must not appear",
        }
        self.details.return_value = self.record

    def tearDown(self):
        self.details_patch.stop()
        self.list_patch.stop()
        super().tearDown()

    def seed(self, **changes):
        values = {
            "endpoint_name": "test-endpoint",
            "coiote_device_id": "remote-id",
            "friendly_name": "Old local name",
            "security_mode": "psk",
            "onboarding_status": "coiote_record_created",
            "psk_identity": "local identity",
            "psk_key": "001122",
        }
        with self.session_factory() as db:
            device = Device(**{**values, **changes})
            db.add(device)
            db.commit()
            return device.id

    def test_last_seen_fetches_only_the_selected_device_without_changing_inventory(self):
        self.seed()
        self.seed(endpoint_name="other", coiote_device_id="other-id")
        with patch("backend.main.get_coiote_device_details", return_value=self.record) as details:
            response = self.client.get("/devices/test-endpoint/last-seen")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"last_seen_at": "2026-11-09T10:02:00Z"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        details.assert_called_once_with("remote-id")
        self.list_devices.assert_not_called()
        with self.session_factory() as db:
            self.assertIsNone(db.query(Device).filter_by(endpoint_name="test-endpoint").one().last_seen_at)

    def test_last_seen_distinguishes_no_contact_and_invalid_contact_data(self):
        self.seed()
        self.seed(endpoint_name="unlinked", coiote_device_id=None)
        with patch("backend.main.get_coiote_device_details") as details:
            self.assertEqual(self.client.get("/devices/missing/last-seen").status_code, 404)
            self.assertEqual(self.client.get("/devices/unlinked/last-seen").status_code, 409)
            details.assert_not_called()
            details.return_value = {**self.record, "lastContactTime": None}
            response = self.client.get("/devices/test-endpoint/last-seen")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"last_seen_at": None})
            details.return_value = {**self.record, "lastContactTime": "invalid"}
            self.assertEqual(self.client.get("/devices/test-endpoint/last-seen").status_code, 502)

    def test_activity_checks_local_devices_without_changing_inventory(self):
        self.seed()
        self.seed(endpoint_name="unknown", coiote_device_id=None)
        self.seed(endpoint_name="old", coiote_device_id="old-id")
        self.seed(endpoint_name="no-contact", coiote_device_id="no-contact-id")
        now = datetime(2026, 11, 9, 10, 4, tzinfo=timezone.utc)
        records = {
            "remote-id": self.record,
            "old-id": {**self.record, "id": "old-id", "lastContactTime": "2026-11-09T10:01:59Z"},
            "no-contact-id": {**self.record, "id": "no-contact-id", "lastContactTime": None},
        }
        with patch("backend.main.get_coiote_device_details", side_effect=records.get) as details:
            with patch("backend.main.datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                response = self.client.get("/device-activity")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {
            "recently_active": 1, "unknown": 2, "checked_at": "2026-11-09T10:04:00Z"
        })
        self.assertEqual(details.call_count, 3)
        self.list_devices.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 4)
            self.assertIsNone(db.query(Device).filter_by(endpoint_name="test-endpoint").one().last_seen_at)

    def test_activity_errors_do_not_report_a_successful_count(self):
        self.seed()
        with patch("backend.main.get_coiote_device_details") as details:
            details.side_effect = httpx.ConnectError("provider secret")
            response = self.client.get("/device-activity")
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("provider secret", response.text)
            details.side_effect = None
            details.return_value = {**self.record, "lastContactTime": "invalid"}
            self.assertEqual(self.client.get("/device-activity").status_code, 502)
            details.return_value = {**self.record, "id": "wrong-id"}
            self.assertEqual(self.client.get("/device-activity").status_code, 502)
            details.return_value = {**self.record, "lastContactTime": "2999-01-01T00:00:00Z"}
            self.assertEqual(self.client.get("/device-activity").status_code, 502)

    def test_list_and_details_use_stored_records_without_provider_requests(self):
        stored_at = datetime(2026, 11, 9, 10, 2, tzinfo=timezone.utc)
        local_id = self.seed(
            connection_status="unknown", refreshed_at=stored_at,
            last_seen_at=stored_at, connector_type="management", domain="/stored/",
        )
        self.seed(endpoint_name="second-endpoint", coiote_device_id="second-id")
        self.list_devices.side_effect = httpx.ConnectError("provider unavailable")
        self.details.side_effect = httpx.ConnectError("provider unavailable")
        for _ in range(2):
            listed = self.client.get("/devices")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.headers["cache-control"], "no-store")
            self.assertEqual(
                [item["endpoint_name"] for item in listed.json()],
                ["test-endpoint", "second-endpoint"],
            )
            response = self.client.get("/devices/test-endpoint")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            body = response.json()
            self.assertEqual(body["local"]["id"], local_id)
            self.assertEqual(body["local"]["friendly_name"], "Old local name")
            self.assertEqual(body["local"]["refreshed_at"], "2026-11-09T10:02:00Z")
            self.assertEqual(body["local"]["last_seen_at"], "2026-11-09T10:02:00Z")
            self.assertEqual(body["coiote_snapshot"]["domain"], "/stored/")
            self.assertEqual(
                body["coiote_snapshot"]["fetched_at"], body["local"]["refreshed_at"]
            )
            self.assertNotIn("psk_key", response.text)
            self.assertNotIn("psk_identity", response.text)
            self.assertNotIn("local identity", response.text)
        self.list_devices.assert_not_called()
        self.details.assert_not_called()

    def test_created_device_is_immediately_readable_without_rediscovery(self):
        created = self.client.post("/devices", json=self.payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["coiote_device_id"], "test-endpoint")
        self.list_devices.side_effect = httpx.ConnectError("provider unavailable")
        response = self.client.get("/devices/test-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["local"], created.json())
        self.assertIsNone(response.json()["coiote_snapshot"])
        self.assertIsNone(response.json()["local"]["refreshed_at"])
        self.assertEqual(self.client.get("/devices").json(), [created.json()])
        self.create.assert_called_once()
        self.list_devices.assert_not_called()
        self.details.assert_not_called()

    def test_explicit_sync_refreshes_and_preserves_local_identity_and_secrets(self):
        local_id = self.seed()
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        first = self.client.get("/devices")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["cache-control"], "no-store")
        device = first.json()[0]
        self.assertEqual(device["id"], local_id)
        self.assertEqual(device["friendly_name"], "Current remote name")
        self.assertEqual(device["onboarding_status"], "coiote_record_created")
        self.assertEqual(device["connection_status"], "unknown")
        self.assertEqual(device["last_seen_at"], "2026-11-09T10:02:00Z")
        self.assertIsNotNone(device["refreshed_at"])
        self.assertNotIn("status", device)
        self.assertNotIn("psk_key", device)
        self.assertNotIn("psk_identity", device)
        self.record["friendlyName"] = "Changed again"
        self.record["lastContactTime"] = "2026-11-09T10:03:00Z"
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        second = self.client.get("/devices")
        self.assertEqual(second.json()[0]["friendly_name"], "Changed again")
        self.assertEqual(second.json()[0]["last_seen_at"], "2026-11-09T10:03:00Z")
        self.assertEqual(self.list_devices.call_count, 2)
        self.assertEqual(self.details.call_count, 2)
        self.details.assert_called_with("remote-id")
        with self.session_factory() as db:
            device = db.query(Device).one()
            self.assertEqual(device.id, local_id)
            self.assertEqual(device.friendly_name, "Changed again")
            self.assertEqual(device.psk_identity, "local identity")
            self.assertEqual(device.psk_key, "001122")

    def test_detail_only_returns_allowlisted_fields_after_explicit_import(self):
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices/test-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(
            body["local"]["onboarding_status"], "synced_from_coiote"
        )
        self.assertEqual(set(body), {"local", "coiote_snapshot"})
        snapshot = body["coiote_snapshot"]
        self.assertEqual(set(snapshot), {
            "fetched_at", "device_id", "endpoint_name", "friendly_name",
            "connector_type", "domain", "security_mode",
        })
        self.assertEqual(snapshot["device_id"], "remote-id")
        self.assertEqual(snapshot["fetched_at"], body["local"]["refreshed_at"])
        self.assertNotIn("upstream secret", response.text)
        self.assertNotIn("upstream identity", response.text)
        self.assertNotIn("unexpected", response.text)
        with self.session_factory() as db:
            self.assertIsNone(db.query(Device).one().psk_key)
            self.assertIsNone(db.query(Device).one().psk_identity)

    def test_sync_updates_existing_and_imports_without_using_id_as_endpoint(self):
        local_id = self.seed()
        self.list_devices.return_value = ["remote-id", "second-id"]
        second = {
            **self.record,
            "id": "second-id",
            "properties": {"endpointName": "second-endpoint"},
        }
        self.details.side_effect = [self.record, second]
        response = self.client.post("/sync-devices")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "synced": ["second-endpoint"], "updated": ["test-endpoint"],
            "missing_from_coiote": [],
        })
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 2)
            self.assertEqual(db.get(Device, local_id).friendly_name, "Current remote name")
            imported = db.query(Device).filter_by(endpoint_name="second-endpoint").one()
            self.assertEqual(imported.coiote_device_id, "second-id")

    def test_failed_refresh_returns_errors_and_does_not_commit_partial_updates(self):
        self.seed()
        self.list_devices.return_value = ["remote-id", "second-id"]
        request = httpx.Request("GET", "https://example.invalid/devices/second-id")
        failure = httpx.HTTPStatusError(
            "secret error", request=request,
            response=httpx.Response(503, request=request, text="secret error"),
        )
        self.details.side_effect = [self.record, failure]
        response = self.client.post("/sync-devices")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"], "Coiote request failed (HTTP 503)"
        )
        self.assertNotIn("secret error", response.text)
        self.assertNotIn("Old local name", response.text)
        with self.session_factory() as db:
            device = db.query(Device).one()
            self.assertEqual(device.friendly_name, "Old local name")
            self.assertIsNone(device.refreshed_at)

    def test_transport_failure_is_explicit(self):
        self.seed()
        self.list_devices.side_effect = httpx.ConnectError("secret connection details")
        response = self.client.post("/sync-devices")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Coiote request could not be completed")
        self.assertNotIn("secret connection details", response.text)

    def test_invalid_payloads_fail_without_writes(self):
        invalid_records = [
            {**self.record, "id": "wrong-id"},
            {**self.record, "properties": {}},
            {**self.record, "lastContactTime": "invalid"},
            {**self.record, "lastContactTime": "2026-11-09T10:00:00"},
            {**self.record, "securityMode": None},
        ]
        missing_time = self.record.copy()
        del missing_time["firstRegisterTime"]
        invalid_records.append(missing_time)
        for record in invalid_records:
            with self.subTest(record=record):
                self.details.return_value = record
                self.assertEqual(self.client.post("/sync-devices").status_code, 502)
                with self.session_factory() as db:
                    self.assertEqual(db.query(Device).count(), 0)

    def test_invalid_list_is_rejected(self):
        for ids in ({}, ["remote-id", "remote-id"], [1], [""]):
            with self.subTest(ids=ids):
                self.list_devices.return_value = ids
                self.assertEqual(self.client.post("/sync-devices").status_code, 502)
        self.details.assert_not_called()

    def test_never_connected_is_not_inferred_as_offline_or_running(self):
        self.record.update(
            firstRegisterTime=None, lastRegisterTime=None, lastContactTime=None
        )
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices")
        self.assertEqual(response.json()[0]["connection_status"], "never_connected")
        self.assertIsNone(response.json()[0]["last_seen_at"])
        self.record["lastContactTime"] = "2026-11-09T10:00:00Z"
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices")
        self.assertEqual(response.json()[0]["connection_status"], "unknown")

    def test_missing_remote_record_is_explicit_and_local_record_is_preserved(self):
        local_id = self.seed(connection_status="unknown", domain="/old/")
        self.list_devices.return_value = []
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices/test-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["coiote_snapshot"])
        device = response.json()["local"]
        self.assertEqual(device["connection_status"], "not_in_coiote")
        self.assertEqual(device["onboarding_status"], "coiote_record_created")
        self.assertIsNotNone(device["refreshed_at"])
        with self.session_factory() as db:
            device = db.get(Device, local_id)
            self.assertIsNotNone(device)
            self.assertIsNone(device.domain)
            self.assertEqual(device.psk_key, "001122")
        self.details.assert_not_called()

    def test_unknown_endpoint_returns_404_without_importing_or_contacting_coiote(self):
        response = self.client.get("/devices/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/devices/test-endpoint").status_code, 404)
        self.assertEqual(self.client.get("/devices").json(), [])
        self.list_devices.assert_not_called()
        self.details.assert_not_called()

    def test_remote_rename_preserves_local_id_and_does_not_duplicate(self):
        local_id = self.seed()
        self.record["properties"]["endpointName"] = "renamed-endpoint"
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices/renamed-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["local"]["id"], local_id)
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 1)

    def test_conflicting_local_identifiers_fail_without_changes(self):
        self.seed()
        self.seed(endpoint_name="other-endpoint", coiote_device_id="other-id")
        self.record["properties"]["endpointName"] = "other-endpoint"
        response = self.client.post("/sync-devices")
        self.assertEqual(response.status_code, 409)
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 2)
            self.assertTrue(all(d.refreshed_at is None for d in db.query(Device).all()))

    def test_remote_null_name_clears_old_name_and_offsets_are_normalized(self):
        self.seed()
        self.record["friendlyName"] = None
        self.record["lastContactTime"] = "2026-11-09T12:02:00+02:00"
        self.assertEqual(self.client.post("/sync-devices").status_code, 200)
        response = self.client.get("/devices")
        self.assertIsNone(response.json()[0]["friendly_name"])
        self.assertEqual(response.json()[0]["last_seen_at"], "2026-11-09T10:02:00Z")

    def test_patch_and_delete_keep_remote_first_behavior_and_renamed_response(self):
        self.seed()
        with patch("backend.main.update_coiote_device") as update:
            response = self.client.patch(
                "/devices/test-endpoint", json={"friendly_name": "Renamed"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["onboarding_status"], "coiote_record_created")
            self.assertNotIn("status", response.json())
            update.assert_called_once_with("remote-id", "Renamed")
        with patch("backend.main.delete_coiote_device") as delete:
            delete.side_effect = httpx.ConnectError("secret")
            self.assertEqual(
                self.client.delete("/devices/test-endpoint").status_code, 502
            )
            with self.session_factory() as db:
                self.assertEqual(db.query(Device).count(), 1)
            delete.side_effect = None
            self.assertEqual(
                self.client.delete("/devices/test-endpoint").status_code, 204
            )
            with self.session_factory() as db:
                self.assertEqual(db.query(Device).count(), 0)


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_sqlite_rows_survive_idempotent_migration(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE devices (
                        id INTEGER PRIMARY KEY, endpoint_name VARCHAR UNIQUE,
                        friendly_name VARCHAR, security_mode VARCHAR,
                        psk_identity VARCHAR, psk_key VARCHAR,
                        coiote_device_id VARCHAR, status VARCHAR, created_at DATETIME
                    )
                """))
                connection.execute(text("""
                    INSERT INTO devices (id, endpoint_name, status, psk_key)
                    VALUES (2, 'legacy-device', 'coiote_record_created', '001122')
                """))
            initialize_database(engine)
            initialize_database(engine)
            self.assertIn("groups", inspect(engine).get_table_names())
            with sessionmaker(bind=engine)() as db:
                db.add(Group(name="Migration test group"))
                db.commit()
            initialize_database(engine)
            self.assertIn(
                "connection_status",
                {column["name"] for column in inspect(engine).get_columns("devices")},
            )
            with sessionmaker(bind=engine)() as db:
                device = db.get(Device, 2)
                self.assertEqual(device.endpoint_name, "legacy-device")
                self.assertEqual(device.onboarding_status, "coiote_record_created")
                self.assertEqual(device.psk_key, "001122")
                self.assertIsNone(device.connection_status)
                self.assertEqual(db.query(Group).one().name, "Migration test group")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
