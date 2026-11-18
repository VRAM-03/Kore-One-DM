import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.device_server_settings import ServerSettingTask
from backend.models import Device


class ServerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        main.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.sessions() as db:
            db.add_all([
                Device(endpoint_name="sensor", coiote_device_id="remote"),
                Device(endpoint_name="other", coiote_device_id="other-remote"),
            ])
            db.commit()

        def get_db():
            with self.sessions() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_db
        self.addCleanup(main.app.dependency_overrides.pop, main.get_db)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.requests = []
        self.values = {
            6: "true", 7: "U", 14: "0", 17: "5", 18: "60", 19: "86400",
            20: "1", 23: "false",
        }
        self.names = {
            6: "Notification Storing", 7: "Binding", 14: "Initial Registration Delay Timer",
            17: "Communication Retry Count", 18: "Communication Retry Timer",
            19: "Communication Sequence Delay Timer",
            20: "Communication Sequence Retry Count", 23: "Mute Send",
        }
        self.report_status = "Success"
        self.observed_at = "2026-11-17T09:00:02Z"
        self.submit_error = False
        self.read_only = set()
        self.absent = set()
        self.active_resource = 6
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(self.handle_request)
        client_patch = patch(
            "backend.device_operations.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=transport, **kwargs),
        )
        client_patch.start()
        self.addCleanup(client_patch.stop)
        token_patch = patch("backend.device_operations.get_coiote_token", return_value="test")
        token_patch.start()
        self.addCleanup(token_patch.stop)
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("COIOTE_SHORT_SERVER_ID", None)
        self.url = "/devices/sensor/server-settings"

    def key(self, resource_id):
        return f"LwM2M Server.3.{self.names[resource_id]}"

    def parameter(self, name, value):
        return {"name": name, "value": value, "updateTime": self.observed_at}

    def handle_request(self, request):
        self.requests.append(request)
        if request.method == "POST":
            definition = json.loads(request.content)["taskDefinition"]
            resource = definition["operations"][-1]["read"]["key"]
            self.active_resource = next(i for i in self.names if self.key(i) == resource)
            if self.submit_error:
                raise httpx.ReadTimeout("Lost submission response", request=request)
            return httpx.Response(201, json="provider-task")
        if "/taskReports/" in request.url.path:
            return httpx.Response(200, json={
                "taskId": "provider-task", "deviceId": "remote", "status": self.report_status,
                "startTime": "2026-11-17T09:00:00Z",
            })
        if "/instantiatedResources/" in request.url.path:
            return httpx.Response(200, json=[
                {
                    "path": self.key(i), "resourceId": i,
                    "type": "Boolean" if i in (6, 23) else "String" if i == 7 else "UnsignedInteger",
                    "operations": ["Read"] if i in self.read_only else ["Read", "Write"],
                }
                for i in self.names if i not in self.absent
            ])
        resource = request.url.params["parameters"]
        if resource == "LwM2M Server.":
            return httpx.Response(200, json=[
                self.parameter("LwM2M Server.3.Short Server ID", "1"),
                *[self.parameter(self.key(i), v) for i, v in self.values.items()],
            ])
        return httpx.Response(200, json=[
            self.parameter(resource, self.values[self.active_resource]),
        ])

    def write(self, resource_id, value):
        return self.client.patch(f"{self.url}/{resource_id}", json={"value": value})

    def check(self, operation):
        return self.client.get(f"{self.url}/operations/{operation['id']}")

    def test_discovers_typed_values_permissions_and_actual_server_instance(self):
        self.read_only.add(7)
        self.absent.add(23)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        settings = {s["resource_id"]: s for s in response.json()["settings"]}
        self.assertEqual(set(settings), {6, 7, 14, 17, 18, 19, 20, 23})
        self.assertIs(settings[6]["value"], True)
        self.assertEqual(settings[14]["value"], 0)
        self.assertFalse(settings[7]["writable"])
        self.assertFalse(settings[23]["readable"])
        self.assertTrue(settings[18]["resource"].startswith("LwM2M Server.3."))
        self.assertFalse(any(r.method == "POST" for r in self.requests))

    def test_boolean_write_is_persisted_then_verified_without_execute_commands(self):
        response = self.write(6, False)
        self.assertEqual(response.status_code, 202)
        operation = response.json()
        self.assertEqual(operation["state"], "pending")
        definition = json.loads(next(r.content for r in self.requests if r.method == "POST"))["taskDefinition"]
        self.assertEqual(definition["operations"], [
            {"write": {"key": self.key(6), "value": "false"}},
            {"read": {"key": self.key(6)}},
        ])
        self.assertFalse(definition["batchRequests"])
        self.values[6] = "false"
        result = self.check(operation).json()
        self.assertEqual(result["state"], "succeeded")
        self.assertIs(result["value"], False)
        with self.sessions() as db:
            self.assertIsNone(db.get(ServerSettingTask, operation["id"]).active_device)

    def test_all_eight_fields_accept_their_typed_value_and_verify(self):
        for resource_id, value in [
            (6, True), (7, "U"), (14, 0), (17, 5), (18, 60),
            (19, 86400), (20, 1), (23, False),
        ]:
            with self.subTest(resource_id=resource_id):
                response = self.write(resource_id, value)
                self.assertEqual(response.status_code, 202)
                result = self.check(response.json()).json()
                self.assertEqual(result["state"], "succeeded")
                self.assertEqual(type(result["value"]), type(value))
                self.assertEqual(result["value"], value)

    def test_invalid_values_and_executable_or_readonly_ids_never_submit(self):
        for resource_id, value, status in [
            (6, "false", 422), (6, 1, 422), (18, True, 422), (18, 1.5, 422),
            (18, -1, 422), (18, 4294967296, 422), (18, "60", 422),
            (7, "invalid", 422), (7, "UU", 422),
            (0, 1, 404), (4, 1, 404), (8, 1, 404), (9, 1, 404), (16, True, 404),
        ]:
            with self.subTest(resource_id=resource_id, value=value):
                self.assertEqual(self.write(resource_id, value).status_code, status)
        self.assertFalse(any(r.method == "POST" for r in self.requests))

    def test_write_rechecks_device_permissions(self):
        self.read_only.add(6)
        self.assertEqual(self.write(6, False).status_code, 409)
        self.assertFalse(any(r.method == "POST" for r in self.requests))

    def test_pending_operation_blocks_another_write_and_is_recovered_by_reload(self):
        operation = self.write(6, False).json()
        self.report_status = "InProgress"
        response = self.write(18, 120)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["operation_id"], operation["id"])
        recovered = self.client.get(self.url).json()["unresolved_operation"]
        self.assertEqual(recovered["id"], operation["id"])
        self.assertEqual(self.check(operation).json()["state"], "pending")

    def test_reload_reconciles_a_completed_binding_write_without_resubmission(self):
        operation = self.write(7, "UQ").json()
        self.values[7] = "UQ"
        result = self.client.get(self.url).json()
        self.assertIsNone(result["unresolved_operation"])
        self.assertEqual(result["completed_operation"]["id"], operation["id"])
        self.assertEqual(result["completed_operation"]["state"], "succeeded")
        self.assertEqual(result["completed_operation"]["value"], "UQ")
        self.assertEqual(next(s for s in result["settings"] if s["resource_id"] == 7)["value"], "UQ")
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)
        with self.sessions() as db:
            self.assertIsNone(db.get(ServerSettingTask, operation["id"]).active_device)

    def test_new_write_reconciles_a_completed_previous_operation_before_locking(self):
        previous = self.write(6, False).json()
        self.values[6] = "false"
        response = self.write(18, 120)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["resource_id"], 18)
        self.assertNotEqual(response.json()["id"], previous["id"])
        with self.sessions() as db:
            task = db.get(ServerSettingTask, previous["id"])
            self.assertEqual(task.state, "succeeded")
            self.assertIs(task.value, False)
            self.assertIsNone(task.active_device)
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 2)

    def test_stale_readback_is_not_success_and_mismatch_is_explicit(self):
        operation = self.write(18, 120).json()
        self.observed_at = "2026-11-17T08:59:59Z"
        self.assertEqual(self.check(operation).json()["state"], "pending")
        self.observed_at = "2026-11-17T09:00:02Z"
        result = self.check(operation).json()
        self.assertEqual(result["state"], "verification_failed")
        self.assertEqual(result["value"], 60)
        self.assertIn("differs", result["error"])

    def test_lost_submission_remains_persisted_and_blocks_blind_retry(self):
        self.submit_error = True
        operation = self.write(23, True).json()
        self.assertEqual(operation["state"], "submission_unknown")
        self.assertIn("unconfirmed", operation["error"])
        self.assertEqual(self.write(23, True).status_code, 409)
        self.assertEqual(self.check(operation).json()["state"], "submission_unknown")

    def test_read_only_request_schedules_no_write_and_result_is_scoped_to_device(self):
        response = self.client.post(f"{self.url}/18/read")
        operation = response.json()
        definition = json.loads(next(r.content for r in self.requests if r.method == "POST"))["taskDefinition"]
        self.assertEqual(definition["operations"], [{"read": {"key": self.key(18)}}])
        self.assertEqual(self.check(operation).json()["state"], "succeeded")
        self.assertEqual(self.client.get(
            f"/devices/other/server-settings/operations/{operation['id']}",
        ).status_code, 404)

    def test_failed_provider_report_is_not_verified_success(self):
        operation = self.write(7, "T").json()
        self.report_status = "Warning"
        result = self.check(operation).json()
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["value"])
