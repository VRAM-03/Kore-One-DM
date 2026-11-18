from collections.abc import AsyncIterable
from typing import Any
from urllib.parse import quote

import httpx
from starlette.concurrency import run_in_threadpool

from .coiote_client import COIOTE_BASE_URL, get_coiote_token


async def _headers() -> dict[str, str]:
    token = await run_in_threadpool(get_coiote_token)
    return {"Authorization": f"Bearer {token}"}


def _url(path: str) -> str:
    return f"{COIOTE_BASE_URL}/api/coiotedm/v3{path}"


async def create_firmware_resource(
    resource_name: str, filename: str, domain: str
) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _url("/resources"),
            headers=await _headers(),
            json={
                "name": resource_name,
                "location": {"InternalLocation": {"fileName": filename}},
                "category": "FIRMWARE",
                "domain": domain,
                "expirationTime": "ONE_DAY",
            },
        )
    response.raise_for_status()
    payload = response.json()
    resource_id = payload.get("id") if isinstance(payload, dict) else payload
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("Coiote returned an invalid firmware resource identifier")
    return resource_id


async def upload_firmware_data(
    resource_id: str, content: AsyncIterable[bytes]
) -> None:
    encoded_id = quote(resource_id, safe="")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.put(
            _url(f"/resources/{encoded_id}/data"),
            headers={
                **await _headers(),
                "Content-Type": "application/octet-stream",
            },
            content=content,
        )
    response.raise_for_status()


async def start_firmware_update(device_id: str, resource_id: str) -> str:
    encoded_device_id = quote(device_id, safe="")
    encoded_resource_id = quote(resource_id, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _url(f"/tasks/upgrade/{encoded_device_id}/{encoded_resource_id}"),
            headers=await _headers(),
        )
    response.raise_for_status()
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text.strip()
    task_id = payload.get("taskId", payload.get("id")) if isinstance(payload, dict) else payload
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Coiote returned an invalid firmware task identifier")
    return task_id


async def get_firmware_task_report(task_id: str, device_id: str) -> dict[str, Any]:
    encoded_task_id = quote(task_id, safe="")
    encoded_device_id = quote(device_id, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _url(f"/taskReports/{encoded_task_id}/{encoded_device_id}"),
            headers=await _headers(),
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Coiote returned an invalid firmware task report")
    return payload


async def delete_firmware_resource(resource_id: str) -> None:
    encoded_id = quote(resource_id, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            _url(f"/resources/{encoded_id}"),
            headers=await _headers(),
        )
    response.raise_for_status()