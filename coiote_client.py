import os
import math
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv() # to get the required variable values from .env file

COIOTE_BASE_URL = os.getenv("COIOTE_BASE_URL")
COIOTE_USERNAME = os.getenv("COIOTE_USERNAME")
COIOTE_PASSWORD = os.getenv("COIOTE_PASSWORD")

_token_lock = Lock()
_cached_token: str | None = None
_token_deadline = 0.0


def _authorization_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_coiote_token() -> str:
    """Reuses an in-memory token until shortly before its documented expiry."""
    global _cached_token, _token_deadline
    with _token_lock:
        if _cached_token is not None and monotonic() < _token_deadline:
            return _cached_token

        _cached_token = None
        _token_deadline = 0.0
        started_at = monotonic()
        response = httpx.post(
            f"{COIOTE_BASE_URL}/api/auth/oauth_password",
            data={
                "grant_type": "password",
                "username": COIOTE_USERNAME,
                "password": COIOTE_PASSWORD,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            token = payload.get("access_token")
            lifetime = payload.get("expires_in")
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise ValueError
            if isinstance(lifetime, bool) or not isinstance(lifetime, (str, int, float)):
                raise ValueError
            expires_in = float(lifetime)
            if not math.isfinite(expires_in) or expires_in <= 0:
                raise ValueError
            if str(payload.get("token_type", "")).lower() != "bearer":
                raise ValueError
        except (ValueError, OverflowError):
            raise httpx.RequestError(
                "Coiote returned an invalid authentication response"
            ) from None

        # Count network time against the lifetime and reserve a short safety margin.
        deadline = started_at + expires_in - min(30.0, expires_in * 0.1)
        if monotonic() >= deadline:
            raise httpx.RequestError("Coiote authentication token expired before use")
        _cached_token = token
        _token_deadline = deadline
        return token


def _check_response(response: httpx.Response, token: str) -> None:
    if response.status_code == 401:
        global _cached_token, _token_deadline
        with _token_lock:
            # A delayed rejection must not discard a newer token from another request.
            if _cached_token == token:
                _cached_token = None
                _token_deadline = 0.0
    response.raise_for_status()


def create_coiote_device(
    endpoint_name: str,
    security_mode: str,
    psk_identity: str,
    psk_key: str,
    friendly_name: str | None = None,
) -> dict[str, Any]:
    """Registers a device on Coiote and returns the created device's JSON."""
    token = get_coiote_token()
    payload = {
        "connectorType": "management",
        "properties": {"endpointName": endpoint_name},
        "domain": "/main/xbrybx34/",
        "securityMode": security_mode,
        "dtlsIdentity": psk_identity,
        "dtlsPsk": {"HexadecimalPsk": psk_key},
    }
    if friendly_name is not None:
        payload["friendlyName"] = friendly_name

    response = httpx.post(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices",
        headers=_authorization_headers(token),
        json=payload,
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def list_coiote_devices() -> list[str]:
    """Returns the list of device IDs registered on Coiote."""
    token = get_coiote_token()
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices",
        headers=_authorization_headers(token),
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def get_coiote_device_details(device_id: str) -> dict[str, Any]:
    """Returns full details for one Coiote device."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def get_coiote_device_resources(device_id: str) -> Any:
    """Fetches stored Device-object resource metadata, without issuing a live Read."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/instantiatedResources/resourcesData/{encoded_id}",
        headers=_authorization_headers(token),
        params={"parameters": "Device"},
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def submit_coiote_read(device_id: str, resource_key: str, operation_id: str) -> Any:
    token = get_coiote_token()
    response = httpx.post(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/tasks/configure/{quote(device_id, safe='')}",
        headers=_authorization_headers(token),
        params={"rerunIfExists": "false"},
        json={"taskDefinition": {
            "operations": [{"read": {"key": resource_key}}],
            "name": f"nexyn-read-{operation_id}",
            "batchRequests": False,
            "executeImmediately": True,
        }},
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def get_coiote_task_report(task_id: str, device_id: str) -> Any:
    token = get_coiote_token()
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/taskReports/"
        f"{quote(task_id, safe='')}/{quote(device_id, safe='')}",
        headers=_authorization_headers(token),
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def submit_coiote_timezone_write(
    device_id: str, resource_key: str, value: str, operation_id: str,
) -> Any:
    token = get_coiote_token()
    response = httpx.post(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/tasks/configure/{quote(device_id, safe='')}",
        headers=_authorization_headers(token),
        params={"rerunIfExists": "false"},
        json={"taskDefinition": {
            "operations": [
                {"write": {"key": resource_key, "value": value}},
                {"read": {"key": resource_key}},
            ],
            "name": f"nexyn-write-{operation_id}",
            "batchRequests": False,
            "executeImmediately": True,
        }},
        timeout=15.0,
    )
    _check_response(response, token)
    return response.json()


def update_coiote_device(
    device_id: str,
    friendly_name: str,
) -> None:
    """Updates a device's friendly name in Coiote."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.put(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
        json={"friendlyName": friendly_name},
        timeout=15.0,
    )
    _check_response(response, token)


def delete_coiote_device(device_id: str) -> None:
    """Deletes one device from Coiote."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.delete(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
        timeout=15.0,
    )
    _check_response(response, token)
