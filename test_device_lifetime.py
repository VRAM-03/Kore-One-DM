import json
import os
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import device_lifetime
import main
from device_lifetime import LifetimeTask
from models import Device


class DeviceLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        main.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(Device(endpoint_name="sensor", coiote_device_id="remote-device"))
            db.add(Device(endpoint_name="other", coiote_device_id="other-remote"))
            db.add(Device(endpoint_name="local-only"))
            db.commit()

        def get_test_db():
            with self.sessions() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_test_db
        self.addCleanup(main.app.dependency_overrides.pop, main.get_db)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.requests = []
        self.servers = [self.parameter("LwM2M Server.3.Short Server ID", "1")]
        self.lifetime = "3600"
        self.updated = "2026-11-16T07:00:02Z"
        self.status = "Success"
        self.error = None
        self.task_number = 0
        self.report_device_id = "remote-device"
        self.schedule_payload = None
        self.cache_error = None
        self.cache_override = None
        self.report_start = "2026-11-16T07:00:00Z"
        self.report_steps = []
        self.cache_times = []
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(self.handle_request)
        client_patch = patch(
            "device_operations.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=transport, **kwargs),
        )
        client_patch.start()
        self.addCleanup(client_patch.stop)
        token_patch = patch("device_operations.get_coiote_token", return_value="test-token")
        token_patch.start()
        self.addCleanup(token_patch.stop)
        url_patch = patch("device_operations.COIOTE_BASE_URL", "https://coiote.invalid")
        url_patch.start()
        self.addCleanup(url_patch.stop)
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("COIOTE_SHORT_SERVER_ID", None)
        interval_patch = patch("device_lifetime.LIFETIME_POLL_INTERVAL_SECONDS", 0.001)
        interval_patch.start()
        self.addCleanup(interval_patch.stop)

    @staticmethod
    def parameter(name, value, updated="2026-11-16T07:00:02Z"):
        return {"name": name, "value": value, "updateTime": updated, "type": "integer"}

    def handle_request(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers["authorization"], "Bearer test-token")
        if request.method == "POST":
            self.task_number += 1
            payload = (
                f"task-{self.task_number}"
                if self.schedule_payload is None else self.schedule_payload
            )
            return httpx.Response(201, json=payload)
        if "/taskReports/" in request.url.path:
            if self.report_steps:
                status = self.report_steps.pop(0)
                if status == 4041:
                    return httpx.Response(404, json={"code": 4041})
            else:
                status = self.status
            if self.error is not None:
                return httpx.Response(404, json={"code": self.error})
            task_id = request.url.raw_path.split(b"?")[0].split(b"/")[-2].decode()
            return httpx.Response(200, json={
                "taskId": task_id, "deviceId": self.report_device_id,
                "status": status, "startTime": self.report_start,
            })
        if request.url.params["parameters"] == "LwM2M Server.":
            return httpx.Response(200, json=self.servers)
        if self.cache_error:
            return httpx.Response(404, json={"code": self.cache_error})
        parameters = self.cache_override
        if parameters is None:
            parameters = [self.parameter(
                request.url.params["parameters"], self.lifetime,
                self.cache_times.pop(0) if self.cache_times else self.updated,
            )]
        return httpx.Response(200, json=parameters)

    def write(self, lifetime=3600):
        return self.client.patch("/devices/sensor/lifetime", json={"lifetime": lifetime})

    def read(self):
        return self.client.post("/devices/sensor/lifetime/read")

    def test_write_schedules_remote_write_trigger_and_read_in_order(self):
        response = self.write()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "succeeded")
        self.assertEqual(response.json()["lifetime"], 3600)
        self.assertTrue(response.json()["matches_requested"])
        request = next(request for request in self.requests if request.method == "POST")
        self.assertEqual(request.url.path, "/api/coiotedm/v3/tasks/configure/remote-device")
        self.assertEqual(request.url.params["rerunIfExists"], "false")
        definition = json.loads(request.content)["taskDefinition"]
        self.assertFalse(definition["batchRequests"])
        self.assertTrue(definition["executeImmediately"])
        self.assertEqual(definition["operations"], [
            {"write": {"key": "LwM2M Server.3.Lifetime", "value": "3600"}},
            {"execute": {"key": "LwM2M Server.3.Registration Update Trigger"}},
            {"read": {"key": "LwM2M Server.3.Lifetime"}},
        ])
        with self.sessions() as db:
            self.assertEqual(db.get(LifetimeTask, "task-1").confirmed_lifetime, 3600)

    def test_read_returns_actual_device_value_in_one_application_request(self):
        self.lifetime = "7200"
        response = self.read()
        request = next(request for request in self.requests if request.method == "POST")
        self.assertEqual(json.loads(request.content)["taskDefinition"]["operations"], [
            {"read": {"key": "LwM2M Server.3.Lifetime"}}
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "succeeded")
        self.assertEqual(response.json()["lifetime"], 7200)
        self.assertIsNone(response.json()["matches_requested"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_pending_and_transient_reports_are_polled_internally_until_complete(self):
        self.report_steps = [4041, "NotStarted", "InProgress", "ErrorRetryable"]
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lifetime"], 3600)
        self.assertEqual(len([
            request for request in self.requests if "/taskReports/" in request.url.path
        ]), 5)
        self.assertEqual(self.task_number, 1)

    def test_offline_device_times_out_without_returning_acceptance_or_stale_value(self):
        self.error = 4041
        started = time.monotonic()
        with patch("device_lifetime.LIFETIME_TIMEOUT_SECONDS", 0.03):
            response = self.write()
        self.assertEqual(response.status_code, 504)
        self.assertIn("may still execute later", response.json()["detail"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.task_number, 1)
        with self.sessions() as db:
            self.assertIsNone(db.get(LifetimeTask, "task-1").confirmed_lifetime)

    def test_timeout_also_bounds_upstream_discovery_request(self):
        async def slow_discovery(*args):
            await device_lifetime.sleep(10)

        with (
            patch("device_lifetime.LIFETIME_TIMEOUT_SECONDS", 0.03),
            patch("device_lifetime.get_parameters", side_effect=slow_discovery),
        ):
            response = self.read()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self.task_number, 0)

    def test_failed_or_partially_successful_task_does_not_confirm_write(self):
        for status in ("Error", "Warning"):
            self.status = status
            response = self.write()
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["state"], "failed")
            self.assertIsNone(response.json()["lifetime"])
        self.assertFalse(any(
            request.url.params.get("parameters") == "LwM2M Server.3.Lifetime"
            for request in self.requests
        ))

    def test_stale_cache_is_not_presented_as_read_result(self):
        self.cache_times = ["2026-11-16T06:59:59Z", "2026-11-16T07:00:01Z"]
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lifetime"], 3600)
        self.assertEqual(response.json()["observed_at"], "2026-11-16T07:00:01Z")
        self.assertEqual(self.task_number, 1)
        self.updated = "2026-11-16T06:59:59Z"
        with patch("device_lifetime.LIFETIME_TIMEOUT_SECONDS", 0.03):
            response = self.write()
        self.assertEqual(response.status_code, 504)

    def test_write_readback_mismatch_is_explicit(self):
        self.lifetime = "100"
        response = self.write()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["state"], "verification_failed")
        self.assertEqual(response.json()["requested_lifetime"], 3600)
        self.assertEqual(response.json()["lifetime"], 100)
        self.assertFalse(response.json()["matches_requested"])

    def test_repeated_read_uses_stored_value_without_any_coiote_requests(self):
        first = self.read().json()
        with self.sessions() as db:
            self.assertEqual(db.get(LifetimeTask, "task-1").confirmed_lifetime, 3600)
        self.requests.clear()
        self.lifetime = "8000"
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), first)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.requests, [])
        with self.sessions() as db:
            self.assertEqual(db.query(LifetimeTask).count(), 1)

    def test_read_uses_latest_write_readback_as_a_read_result(self):
        self.read()
        self.lifetime = "8000"
        self.updated = "2026-11-16T08:00:02Z"
        written = self.write(8000)
        self.assertEqual(written.status_code, 200)
        self.assertEqual(self.task_number, 2)
        self.requests.clear()
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            **written.json(), "operation": "read",
            "requested_lifetime": None, "matches_requested": None,
        })
        self.assertEqual(self.requests, [])

    def test_null_stored_lifetime_falls_back_to_device_and_persists_result(self):
        with self.sessions() as db:
            device = db.query(Device).filter_by(endpoint_name="sensor").one()
            db.add(LifetimeTask(
                task_id="unconfirmed", local_device_id=device.id,
                coiote_device_id=device.coiote_device_id,
                resource="LwM2M Server.3.Lifetime", requested_lifetime=9000,
            ))
            db.commit()
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lifetime"], 3600)
        self.assertEqual(self.task_number, 1)
        self.requests.clear()
        self.assertEqual(self.read().json(), response.json())
        self.assertEqual(self.requests, [])

    def test_stored_lifetime_is_scoped_to_local_and_remote_device_ids(self):
        for local_endpoint, remote_id in (
            ("other", "remote-device"), ("sensor", "old-remote-device"),
        ):
            with self.subTest(local_endpoint=local_endpoint, remote_id=remote_id):
                with self.sessions() as db:
                    db.query(LifetimeTask).delete()
                    device = db.query(Device).filter_by(
                        endpoint_name=local_endpoint
                    ).one()
                    db.add(LifetimeTask(
                        task_id="unrelated", local_device_id=device.id,
                        coiote_device_id=remote_id,
                        resource="LwM2M Server.3.Lifetime",
                        confirmed_lifetime=9000,
                        observed_at=device_lifetime.datetime(
                            2026, 11, 17, tzinfo=device_lifetime.timezone.utc
                        ),
                    ))
                    db.commit()
                self.requests.clear()
                response = self.read()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["lifetime"], 3600)
                self.assertTrue(any(
                    request.method == "POST" for request in self.requests
                ))

    def test_multi_server_discovery_never_guesses_instance_zero(self):
        self.servers.append(self.parameter("LwM2M Server.8.Short Server ID", "9"))
        self.assertEqual(self.write().status_code, 409)
        self.assertFalse(any(request.method == "POST" for request in self.requests))
        os.environ["COIOTE_SHORT_SERVER_ID"] = "9"
        self.assertEqual(self.read().json()["resource"], "LwM2M Server.8.Lifetime")
        os.environ["COIOTE_SHORT_SERVER_ID"] = "99"
        self.assertEqual(self.write().status_code, 409)

    def test_empty_ambiguous_and_invalid_server_configuration(self):
        self.servers = []
        self.assertEqual(self.read().status_code, 409)
        os.environ["COIOTE_SHORT_SERVER_ID"] = "not-a-number"
        self.assertEqual(self.write().status_code, 503)
        os.environ.pop("COIOTE_SHORT_SERVER_ID")
        self.servers = [
            self.parameter("LwM2M Server.3.Short Server ID", "1"),
            self.parameter("LwM2M Server.3.Short Server ID", "2"),
        ]
        self.assertEqual(self.read().status_code, 502)

    def test_invalid_input_rejected_before_upstream_calls(self):
        for value in (0, -1, 4294967296, True, 3.5, "3600", None):
            with self.subTest(value=value):
                self.assertEqual(self.write(value).status_code, 422)
        self.assertEqual(self.requests, [])
        self.lifetime = "4294967295"
        self.assertEqual(self.write(4294967295).status_code, 200)

    def test_missing_and_unregistered_devices_fail_before_upstream_calls(self):
        self.assertEqual(self.client.post("/devices/missing/lifetime/read").status_code, 404)
        self.assertEqual(
            self.client.post("/devices/local-only/lifetime/read").status_code, 409
        )
        self.assertEqual(self.requests, [])

    def test_upstream_http_network_and_malformed_responses_are_explicit(self):
        with patch("device_operations.get_coiote_token", side_effect=httpx.ConnectError("secret")):
            response = self.read()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)
        self.schedule_payload = {"unexpected": "value"}
        self.assertEqual(self.write().status_code, 502)
        with self.sessions() as db:
            self.assertEqual(db.query(LifetimeTask).count(), 0)
        self.schedule_payload = None
        self.error = 4040
        self.assertEqual(self.read().status_code, 404)
        self.error = 9999
        self.assertEqual(self.read().status_code, 502)

    def test_malformed_wrong_device_and_missing_resource_results_fail(self):
        for lifetime in ("abc", "0", "4294967296"):
            self.lifetime = lifetime
            self.assertEqual(self.read().status_code, 502)
        self.lifetime = "3600"
        self.report_device_id = "wrong-device"
        self.assertEqual(self.read().status_code, 502)
        self.report_device_id = "remote-device"
        self.cache_error = 4041
        self.assertEqual(self.read().status_code, 502)
        self.cache_error = None
        self.cache_override = []
        self.assertEqual(self.read().status_code, 502)
        self.cache_override = None
        self.report_start = "2026-11-16T07:00:00"
        self.assertEqual(self.read().status_code, 502)

    def test_remote_device_id_is_url_encoded(self):
        with self.sessions() as db:
            device = db.query(Device).filter_by(endpoint_name="sensor").one()
            device.coiote_device_id = "urn:device/sensor"
            db.commit()
        self.report_device_id = "urn:device/sensor"
        self.assertEqual(self.read().status_code, 200)
        self.assertTrue(all(
            b"urn%3Adevice%2Fsensor" in request.url.raw_path
            for request in self.requests
        ))

    def test_only_write_and_read_apis_are_exposed(self):
        paths = {
            path: set(operations) for path, operations in main.app.openapi()["paths"].items()
            if path.startswith("/devices/{endpoint_name}/lifetime")
        }
        self.assertEqual(paths, {
            "/devices/{endpoint_name}/lifetime": {"patch"},
            "/devices/{endpoint_name}/lifetime/read": {"post"},
        })
        self.assertEqual(
            self.client.get("/devices/sensor/lifetime/tasks/task-1").status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
