import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

from backend import coiote_client as client


class CoioteTokenTests(unittest.TestCase):
    def setUp(self):
        self.clear_cache()
        self.addCleanup(self.clear_cache)
        self.post = self.enterContext(patch("backend.coiote_client.httpx.post"))
        self.clock = self.enterContext(patch("backend.coiote_client.monotonic", return_value=100.0))
        self.post.return_value = self.auth_response()

    def clear_cache(self):
        client._cached_token = None
        client._token_deadline = 0.0

    def auth_response(self, **changes):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.invalid/auth"),
            json={
                "access_token": "test-token", "token_type": "Bearer",
                "expires_in": "300", **changes,
            },
        )

    def test_reuses_token_and_refreshes_at_safety_deadline(self):
        self.assertEqual(client.get_coiote_token(), "test-token")
        self.clock.return_value = 369.0
        self.assertEqual(client.get_coiote_token(), "test-token")
        self.post.assert_called_once()
        self.clock.return_value = 370.0
        self.post.return_value = self.auth_response(access_token="replacement")
        self.assertEqual(client.get_coiote_token(), "replacement")
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15.0)

    def test_concurrent_callers_share_one_login(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            tokens = list(executor.map(lambda _: client.get_coiote_token(), range(16)))
        self.assertEqual(tokens, ["test-token"] * 16)
        self.post.assert_called_once()

    def test_invalid_authentication_payloads_are_explicit_errors(self):
        changes = [
            {"access_token": None}, {"access_token": ""},
            {"access_token": "bad token"}, {"expires_in": None},
            {"expires_in": True}, {"expires_in": 0}, {"expires_in": -1},
            {"expires_in": "bad"}, {"expires_in": "nan"}, {"expires_in": "inf"},
            {"token_type": "Basic"},
        ]
        request = httpx.Request("POST", "https://example.invalid/auth")
        responses = [
            httpx.Response(200, json=[], request=request),
            httpx.Response(200, json={}, request=request),
            httpx.Response(200, text="not JSON: secret", request=request),
        ] + [self.auth_response(**change) for change in changes]
        for response in responses:
            with self.subTest(response=response):
                self.post.return_value = response
                with self.assertRaises(httpx.RequestError) as error:
                    client.get_coiote_token()
                self.assertNotIn("secret", str(error.exception))
                self.assertIsNone(client._cached_token)

    def test_expired_token_is_not_used_when_refresh_fails(self):
        client.get_coiote_token()
        self.clock.return_value = 400.0
        self.post.return_value = httpx.Response(
            401, request=httpx.Request("POST", "https://example.invalid/auth")
        )
        with self.assertRaises(httpx.HTTPStatusError):
            client.get_coiote_token()
        self.assertIsNone(client._cached_token)
        self.post.return_value = self.auth_response(access_token="new")
        self.assertEqual(client.get_coiote_token(), "new")

    def test_transport_failure_is_not_cached(self):
        self.post.side_effect = httpx.ConnectError("unavailable")
        with self.assertRaises(httpx.ConnectError):
            client.get_coiote_token()
        self.assertIsNone(client._cached_token)

    def test_network_time_counts_against_token_lifetime(self):
        self.clock.side_effect = [100.0, 400.0]
        with self.assertRaises(httpx.RequestError):
            client.get_coiote_token()
        self.assertIsNone(client._cached_token)

    def test_401_invalidates_without_retry_and_next_call_logs_in(self):
        with patch("backend.coiote_client.httpx.get") as get:
            get.return_value = httpx.Response(
                401, request=httpx.Request("GET", "https://example.invalid/devices")
            )
            with self.assertRaises(httpx.HTTPStatusError):
                client.list_coiote_devices()
            get.assert_called_once()
            self.assertIsNone(client._cached_token)
            self.post.return_value = self.auth_response(access_token="new")
            get.return_value = httpx.Response(
                200, json=[], request=httpx.Request("GET", "https://example.invalid/devices")
            )
            self.assertEqual(client.list_coiote_devices(), [])
            self.assertEqual(self.post.call_count, 2)

    def test_delayed_401_does_not_invalidate_newer_token(self):
        client.get_coiote_token()
        response = httpx.Response(
            401, request=httpx.Request("GET", "https://example.invalid/devices")
        )
        with self.assertRaises(httpx.HTTPStatusError):
            client._check_response(response, "old-token")
        self.assertEqual(client.get_coiote_token(), "test-token")
        self.post.assert_called_once()

    def test_all_operations_share_token_and_preserve_encoded_paths(self):
        ok = httpx.Response(
            200, json={}, request=httpx.Request("GET", "https://example.invalid/devices")
        )
        self.post.side_effect = [self.auth_response(), ok]
        with (
            patch("backend.coiote_client.httpx.get", return_value=ok) as get,
            patch("backend.coiote_client.httpx.put", return_value=ok) as put,
            patch("backend.coiote_client.httpx.delete", return_value=ok) as delete,
        ):
            client.create_coiote_device("endpoint", "psk", "identity", "aabb", "Name")
            client.list_coiote_devices()
            client.get_coiote_device_details("id/with space")
            client.update_coiote_device("id/with space", "New name")
            client.delete_coiote_device("id/with space")
            self.assertEqual(self.post.call_count, 2)
            for call in [self.post.call_args, *get.call_args_list,
                         put.call_args, delete.call_args]:
                self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer test-token"})
                self.assertEqual(call.kwargs["timeout"], 15.0)
            for mock in (get, put, delete):
                self.assertTrue(mock.call_args.args[0].endswith("/id%2Fwith%20space"))

    def test_write_failures_are_never_retried(self):
        client.get_coiote_token()
        for method, operation in (
            ("post", lambda: client.create_coiote_device("endpoint", "psk", "identity", "aabb")),
            ("post", lambda: client.submit_coiote_timezone_write(
                "endpoint", "Device.0.Timezone", "Etc/UTC", "op-1"
            )),
            ("put", lambda: client.update_coiote_device("endpoint", "Name")),
            ("delete", lambda: client.delete_coiote_device("endpoint")),
        ):
            for status in (401, 403, 500):
                with self.subTest(method=method, status=status):
                    response = httpx.Response(
                        status, request=httpx.Request(method, "https://example.invalid/devices")
                    )
                    with (
                        patch("backend.coiote_client.get_coiote_token", return_value="test-token"),
                        patch(f"backend.coiote_client.httpx.{method}", return_value=response) as send,
                    ):
                        with self.assertRaises(httpx.HTTPStatusError):
                            operation()
                        send.assert_called_once()

    def test_resource_discovery_uses_device_filter_token_and_encoded_identity(self):
        response = httpx.Response(
            200, json=[], request=httpx.Request("GET", "https://example.invalid/resources")
        )
        with patch("backend.coiote_client.httpx.get", return_value=response) as get:
            self.assertEqual(client.get_coiote_device_resources("id/with space"), [])
            call = get.call_args
            self.assertTrue(call.args[0].endswith(
                "/instantiatedResources/resourcesData/id%2Fwith%20space"
            ))
            self.assertEqual(call.kwargs["params"], {"parameters": "Device"})
            self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer test-token"})
            self.assertEqual(call.kwargs["timeout"], 15.0)
            self.post.assert_called_once()
            get.return_value = httpx.Response(
                401, request=httpx.Request("GET", "https://example.invalid/resources")
            )
            with self.assertRaises(httpx.HTTPStatusError):
                client.get_coiote_device_resources("id")
            self.assertIsNone(client._cached_token)

    def test_read_submission_and_report_contracts(self):
        submitted = httpx.Response(
            201, json="task:1", request=httpx.Request("POST", "https://example.invalid/tasks")
        )
        self.post.side_effect = [self.auth_response(), submitted]
        self.assertEqual(
            client.submit_coiote_read("device/id", "Device.0.Manufacturer", "op-1"),
            "task:1",
        )
        call = self.post.call_args
        self.assertTrue(call.args[0].endswith("/tasks/configure/device%2Fid"))
        self.assertEqual(call.kwargs["params"], {"rerunIfExists": "false"})
        self.assertEqual(call.kwargs["json"], {"taskDefinition": {
            "operations": [{"read": {"key": "Device.0.Manufacturer"}}],
            "name": "nexyn-read-op-1", "batchRequests": False, "executeImmediately": True,
        }})
        with patch("backend.coiote_client.httpx.get") as get:
            get.return_value = httpx.Response(
                200, json={}, request=httpx.Request("GET", "https://example.invalid/report")
            )
            self.assertEqual(client.get_coiote_task_report("task:1", "device/id"), {})
            self.assertTrue(get.call_args.args[0].endswith(
                "/taskReports/task%3A1/device%2Fid"
            ))
            self.assertEqual(get.call_args.kwargs["timeout"], 15.0)
        self.assertEqual(self.post.call_count, 2)

    def test_timezone_write_is_followed_by_read_back_in_one_task(self):
        submitted = httpx.Response(
            201, json="task:tz", request=httpx.Request("POST", "https://example.invalid/tasks")
        )
        self.post.side_effect = [self.auth_response(), submitted]
        self.assertEqual(client.submit_coiote_timezone_write(
            "device/id", "Device.0.Timezone", "Etc/UTC", "op-tz"
        ), "task:tz")
        call = self.post.call_args
        self.assertTrue(call.args[0].endswith("/tasks/configure/device%2Fid"))
        self.assertEqual(call.kwargs["params"], {"rerunIfExists": "false"})
        self.assertEqual(call.kwargs["json"], {"taskDefinition": {
            "operations": [
                {"write": {"key": "Device.0.Timezone", "value": "Etc/UTC"}},
                {"read": {"key": "Device.0.Timezone"}},
            ],
            "name": "nexyn-write-op-tz", "batchRequests": False, "executeImmediately": True,
        }})
        self.assertEqual(call.kwargs["timeout"], 15.0)


if __name__ == "__main__":
    unittest.main()
