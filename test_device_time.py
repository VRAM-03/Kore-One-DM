import asyncio
import json
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from device_time import TIME_RESOURCES
from models import Device


class DeviceTimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        main.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.sessions() as db:
            db.add(Device(endpoint_name="sensor", coiote_device_id="remote-device"))
            db.add(Device(endpoint_name="local-only"))
            db.commit()

        def get_test_db():
            with self.sessions() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_test_db
        self.addCleanup(main.app.dependency_overrides.pop, main.get_db)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.requests = []
        self.task_id = "task:time/1"
        self.device_id = "remote-device"
        self.status = "Success"
        self.report_steps = []
        self.report_start = "2026-11-16T12:20:00Z"
        self.updated = "2026-11-16T12:20:02Z"
        self.cache_times = []
        self.values = {
            "timezone": "Asia/Kolkata",
            "utc_offset": "+05:30",
            "current_time": "2026-11-16T12:20:02Z",
        }
        self.cache_override = None
        self.task_payload = self.task_id
        self.report_override = None
        self.http_error = None
        self.report_error = None
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(self.handle_request)
        for mock in (
            patch(
                "device_operations.httpx.AsyncClient",
                side_effect=lambda **kwargs: original_client(transport=transport, **kwargs),
            ),
            patch("device_operations.get_coiote_token", return_value="test-token"),
            patch("device_operations.COIOTE_BASE_URL", "https://coiote.invalid"),
            patch("device_time.TIME_POLL_INTERVAL_SECONDS", 0.001),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def handle_request(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers["authorization"], "Bearer test-token")
        if self.http_error is not None:
            return httpx.Response(self.http_error, json={"private": "do-not-expose"})
        if request.method == "POST":
            return httpx.Response(201, json=self.task_payload)
        if "/taskReports/" in request.url.path:
            status = self.report_steps.pop(0) if self.report_steps else self.status
            code = self.report_error if self.report_error is not None else (
                4041 if status == 4041 else None
            )
            if code is not None:
                return httpx.Response(404, json={"code": code})
            return httpx.Response(200, json=(
                self.report_override if self.report_override is not None else {
                    "taskId": self.task_id, "deviceId": self.device_id,
                    "status": status, "startTime": self.report_start,
                }
            ))
        self.assertEqual(request.url.params["parameters"], "Device.0.")
        updated = self.cache_times.pop(0) if self.cache_times else self.updated
        parameters = self.cache_override
        if parameters is None:
            parameters = [
                {"name": TIME_RESOURCES[field], "value": value,
                 "updateTime": updated, "type": "time" if field == "current_time" else "string"}
                for field, value in self.values.items()
            ]
        return httpx.Response(200, json=parameters)

    def send(self, body):
        return self.client.patch("/devices/sensor/time", json=body)

    def definition(self):
        request = next(request for request in self.requests if request.method == "POST")
        self.assertEqual(request.url.path, "/api/coiotedm/v3/tasks/configure/remote-device")
        self.assertEqual(request.url.params["rerunIfExists"], "false")
        return json.loads(request.content)["taskDefinition"]

    def test_optional_fields_and_combined_write_use_documented_coiote_operations(self):
        supplied = {
            "timezone": "Asia/Kolkata",
            "utc_offset": "+05:30",
            "current_time": "2026-11-16T17:50:00+05:30",
        }
        normalized = dict(supplied, current_time="2026-11-16T12:20:00Z")
        for body in ({key: value} for key, value in supplied.items()):
            with self.subTest(body=body):
                self.requests.clear()
                response = self.send(body)
                self.assertEqual(response.status_code, 200, response.text)
                field = next(iter(body))
                definition = self.definition()
                self.assertFalse(definition["batchRequests"])
                self.assertTrue(definition["executeImmediately"])
                self.assertEqual(definition["operations"], [
                    {"write": {"key": TIME_RESOURCES[field], "value": normalized[field]}},
                    {"read": {"key": TIME_RESOURCES[field]}},
                ])
                result = response.json()
                self.assertEqual(result["requested"], {field: normalized[field]})
                self.assertEqual(result["values"], {field: self.values[field]})
                self.assertEqual(set(result["observed_at"]), {field})
                self.assertEqual(result["state"], "succeeded")
                self.assertEqual(result["operation"], "write")
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.requests.clear()
        response = self.send(supplied)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.definition()["operations"], [
            *[{"write": {"key": TIME_RESOURCES[field], "value": normalized[field]}}
              for field in TIME_RESOURCES],
            *[{"read": {"key": resource}} for resource in TIME_RESOURCES.values()],
        ])
        self.assertEqual(response.json()["values"], self.values)
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)

    def test_empty_or_absent_body_reads_all_three_from_device_without_writes(self):
        for body in ({}, None):
            with self.subTest(body=body):
                self.requests.clear()
                response = (
                    self.send(body) if body is not None
                    else self.client.patch("/devices/sensor/time")
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.definition()["operations"], [
                    {"read": {"key": resource}} for resource in TIME_RESOURCES.values()
                ])
                result = response.json()
                self.assertEqual(result["operation"], "read")
                self.assertEqual(result["requested"], {})
                self.assertEqual(result["values"], self.values)
                self.assertEqual(result["task_id"], self.task_id)

    def test_invalid_values_and_unknown_fields_never_reach_coiote(self):
        invalid = [
            {"timezone": value} for value in (None, "", "Moon/Unknown", "../UTC", 123)
        ] + [
            {"utc_offset": value}
            for value in (None, "5:30", "+24:00", "+05:60", "Z", True, "+05:30\n")
        ] + [
            {"current_time": value}
            for value in (
                None, 1794831600, "1794831600", "2026-11-16T12:20:00",
                "2026-02-30T12:20:00Z", "2026-11-16T12:20:00.123Z",
                "2026-11-16T12:20:00+24:00",
            )
        ] + [{"time_zone": "UTC"}]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.send(body).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_missing_timezone_database_is_a_server_error_not_invalid_user_input(self):
        with patch(
            "device_time.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing data")
        ):
            response = self.send({"timezone": "Asia/Kolkata"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Install tzdata", response.json()["detail"])
        self.assertEqual(self.requests, [])

    def test_pending_reports_and_stale_cache_are_polled_without_resubmitting(self):
        self.report_steps = [4041, "NotStarted", "InProgress", "ErrorRetryable"]
        self.cache_times = ["2026-11-16T12:19:59Z", self.updated]
        response = self.send({})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)
        self.assertEqual(sum("/taskReports/" in r.url.path for r in self.requests), 6)
        self.assertEqual(response.json()["observed_at"]["current_time"], self.updated)

    def test_timeout_does_not_return_stale_settings_or_imply_rollback(self):
        for case in ("offline", "stale"):
            with self.subTest(case=case):
                self.requests.clear()
                self.report_error = 4041 if case == "offline" else None
                self.updated = "2026-11-16T12:19:59Z"
                started = time.monotonic()
                with patch("device_time.TIME_TIMEOUT_SECONDS", 0.05):
                    response = self.send({"timezone": "Asia/Kolkata"})
                self.assertEqual(response.status_code, 504)
                self.assertLess(time.monotonic() - started, 1)
                self.assertIn("may still execute later", response.json()["detail"])
                self.assertNotIn("values", response.json())
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(sum(r.method == "POST" for r in self.requests), 1)

    def test_timeout_also_bounds_task_submission(self):
        async def slow_request(*args, **kwargs):
            await asyncio.sleep(10)

        with (
            patch("device_time.TIME_TIMEOUT_SECONDS", 0.03),
            patch("device_time.coiote_request", side_effect=slow_request),
        ):
            self.assertEqual(self.send({}).status_code, 504)

    def test_failed_or_partial_tasks_report_possible_partial_writes(self):
        for status in ("Error", "Warning"):
            with self.subTest(status=status):
                self.requests.clear()
                self.status = status
                response = self.send({"timezone": "Asia/Kolkata"})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["state"], "failed")
                self.assertIn("Earlier writes may have succeeded", response.json()["detail"])
                self.assertNotIn("values", response.json())
                self.assertFalse(any("/cachedDataModels/" in r.url.path for r in self.requests))

    def test_mismatch_returns_actual_value_and_clock_is_allowed_to_advance(self):
        for field, actual in (("timezone", "UTC"), ("utc_offset", "+00:00")):
            with self.subTest(field=field):
                original = self.values[field]
                self.values[field] = actual
                response = self.send({field: original})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["state"], "verification_failed")
                self.assertEqual(response.json()["values"], {field: actual})
                self.values[field] = original
        self.values["current_time"] = "2026-11-16T12:20:03.000Z"
        response = self.send({"current_time": "2026-11-16T12:20:00Z"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["values"]["current_time"], "2026-11-16T12:20:03Z")

    def test_unavailable_optional_resources_only_fail_when_selected(self):
        del self.values["timezone"]
        self.assertEqual(self.send({}).status_code, 502)
        self.assertEqual(self.send({"utc_offset": "+05:30"}).status_code, 200)

    def test_malformed_reports_and_values_are_explicit_errors(self):
        for task_id in ("", {"id": self.task_id}, None):
            self.task_payload = task_id
            self.assertEqual(self.send({}).status_code, 502)
        self.task_payload = self.task_id
        for field, value in (
            ("taskId", "different-task"), ("deviceId", "different-device"),
            ("status", "Unexpected"), ("startTime", "2026-11-16T12:20:00"),
        ):
            self.report_override = {
                "taskId": self.task_id, "deviceId": self.device_id,
                "status": self.status, "startTime": self.report_start,
                field: value,
            }
            self.assertEqual(self.send({}).status_code, 502)
        self.report_override = None
        for current_time in ("invalid", "2026-11-16T12:20:00"):
            self.values["current_time"] = current_time
            self.assertEqual(self.send({}).status_code, 502)
        self.values["current_time"] = "2026-11-16T12:20:02Z"
        self.cache_override = [{
            "name": TIME_RESOURCES["timezone"], "value": "UTC",
            "type": "string", "updateTime": self.updated,
        }] * 2
        self.assertEqual(self.send({"timezone": "UTC"}).status_code, 502)
        self.cache_override = None
        self.updated = "2026-11-16T12:20:02"
        self.assertEqual(self.send({}).status_code, 502)

    def test_network_http_and_missing_task_errors_are_sanitized(self):
        with patch(
            "device_operations.get_coiote_token",
            side_effect=httpx.ConnectError("do-not-expose"),
        ):
            response = self.send({})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("do-not-expose", response.text)
        self.http_error = 401
        response = self.send({})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("do-not-expose", response.text)
        self.http_error = None
        self.report_error = 4040
        self.assertEqual(self.send({}).status_code, 404)
        self.report_error = 9999
        self.assertEqual(self.send({}).status_code, 502)

    def test_device_lookup_and_remote_ids_are_handled_before_or_at_coiote_boundary(self):
        for endpoint, status in (("missing", 404), ("local-only", 409)):
            self.assertEqual(
                self.client.patch(f"/devices/{endpoint}/time", json={}).status_code,
                status,
            )
        self.assertEqual(self.requests, [])
        self.device_id = "urn:device/sensor"
        with self.sessions() as db:
            db.query(Device).filter_by(endpoint_name="sensor").one().coiote_device_id = self.device_id
            db.commit()
        response = self.send({})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(
            b"urn%3Adevice%2Fsensor" in request.url.raw_path for request in self.requests
        ))
        report_request = next(r for r in self.requests if "/taskReports/" in r.url.path)
        self.assertIn(b"task%3Atime%2F1", report_request.url.raw_path)


if __name__ == "__main__":
    unittest.main()
