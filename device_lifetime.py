"""Coiote-backed LwM2M connection lifetime reads and writes."""

import os
import re
from asyncio import TimeoutError as AsyncTimeoutError, sleep, wait_for
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from database import Base, get_db
from device_operations import (
    DataModelParameter,
    TaskReport,
    coiote_request,
    get_coiote_task_report,
    get_parameters,
    registered_device,
)
from device_sync import coiote_request_error
from models import Device

router = APIRouter(tags=["Device lifetime"])
LIFETIME_TIMEOUT_SECONDS = 30.0
LIFETIME_POLL_INTERVAL_SECONDS = 1.0

# Database model for tracking device lifetime tasks
class LifetimeTask(Base):
    __tablename__ = "device_lifetime_tasks"

    task_id = Column(String, primary_key=True)
    local_device_id = Column(Integer, nullable=False, index=True)
    coiote_device_id = Column(String, nullable=False)
    resource = Column(String, nullable=False)
    requested_lifetime = Column(Integer, nullable=True)
    confirmed_lifetime = Column(Integer, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)


class DeviceLifetimeUpdate(BaseModel):
    lifetime: int = Field(
        strict=True, ge=1, le=4294967295,
        description="LwM2M registration lifetime in seconds (unsigned 32-bit integer)",
    )

# Result of a lifetime operation for a device
class LifetimeResult(BaseModel):
    task_id: str
    device_id: str
    resource: str
    operation: Literal["read", "write"]
    state: Literal["succeeded", "failed", "verification_failed"]
    requested_lifetime: int | None = None
    lifetime: int | None = None
    matches_requested: bool | None = None
    observed_at: datetime | None = None


def discover_server_resource(parameters: list[DataModelParameter]) -> str:
    servers: dict[int, int] = {}
    for parameter in parameters:
        match = re.fullmatch(
            r"LwM2M Server\.(0|[1-9][0-9]*)\.Short Server ID", parameter.name
        )
        if match:
            instance = int(match[1])
            if (
                instance > 65534 or instance in servers
                or not re.fullmatch(r"[0-9]{1,5}", parameter.value)
                or not 1 <= int(parameter.value) <= 65535
            ):
                raise ValueError("Invalid Coiote Server instance")
            servers[instance] = int(parameter.value)

    configured_id = os.getenv("COIOTE_SHORT_SERVER_ID")
    if configured_id is not None:
        if (
            re.fullmatch(r"[0-9]{1,5}", configured_id) is None
            or not 1 <= int(configured_id) <= 65535
        ):
            raise HTTPException(
                status_code=503, detail="COIOTE_SHORT_SERVER_ID must be between 1 and 65535"
            )
        candidates = [
            instance for instance, server_id in servers.items()
            if server_id == int(configured_id)
        ]
    else:
        candidates = list(servers)

    if len(candidates) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot uniquely identify Coiote's LwM2M Server instance. "
                "Populate its Short Server ID in Coiote's data model and set "
                "COIOTE_SHORT_SERVER_ID when the device has multiple servers."
            ),
        )
    return f"LwM2M Server.{candidates[0]}.Lifetime"


def task_result(
    task: LifetimeTask,
    state: Literal["succeeded", "failed", "verification_failed"],
    **kwargs: Any,
) -> LifetimeResult:
    return LifetimeResult(
        task_id=task.task_id,
        device_id=task.coiote_device_id,
        resource=task.resource,
        operation="read" if task.requested_lifetime is None else "write",
        requested_lifetime=task.requested_lifetime,
        state=state,
        **kwargs,
    )


async def schedule_lifetime(
    device: Device, lifetime: int | None, db: Session
) -> LifetimeTask:
    try:
        resource = discover_server_resource(
            await get_parameters(device.coiote_device_id, "LwM2M Server.")
        )
        operations = []
        if lifetime is not None:
            operations.extend([
                {"write": {"key": resource, "value": str(lifetime)}},
                {"execute": {
                    "key": resource.removesuffix("Lifetime") + "Registration Update Trigger"
                }},
            ])
        operations.append({"read": {"key": resource}})
        task_id = await coiote_request(
            "POST", f"/tasks/configure/{quote(device.coiote_device_id, safe='')}",
            params={"rerunIfExists": "false"},
            json={"taskDefinition": {
                "name": f"nexyn-lifetime-{uuid4().hex}",
                "batchRequests": False,
                "executeImmediately": True,
                "operations": operations,
            }},
        )
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Invalid Coiote task identifier")
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(
            status_code=502, detail="Coiote returned an invalid lifetime response"
        ) from None

    task = LifetimeTask(
        task_id=task_id, local_device_id=device.id,
        coiote_device_id=device.coiote_device_id,
        resource=resource, requested_lifetime=lifetime,
    )
    db.add(task)
    db.commit()
    return task


@router.patch(
    "/devices/{endpoint_name}/lifetime", response_model=LifetimeResult,
    summary="Write connection lifetime and return the verified device value",
    description=(
        "Schedules the Coiote write, registration update and readback, then waits "
        "internally for completion. Returns HTTP 504 after 30 seconds."
    ),
)
async def update_device_lifetime(
    endpoint_name: str, update: DeviceLifetimeUpdate, response: Response,
    db: Session = Depends(get_db),
):
    return await complete_lifetime_request(
        registered_device(endpoint_name, db), update.lifetime, response, db
    )


@router.post(
    "/devices/{endpoint_name}/lifetime/read", response_model=LifetimeResult,
    summary="Read and return the current device lifetime through Coiote",
    description=(
        "Schedules a fresh Coiote device read and retrieves its result internally. "
        "Requires only the endpoint name. Returns HTTP 504 after 30 seconds."
    ),
)
async def read_device_lifetime(
    endpoint_name: str, response: Response, db: Session = Depends(get_db)
):
    return await complete_lifetime_request(
        registered_device(endpoint_name, db), None, response, db
    )


async def get_task_report(task: LifetimeTask) -> TaskReport | None:
    return await get_coiote_task_report(task.task_id, task.coiote_device_id)


async def get_lifetime_result(
    task: LifetimeTask, db: Session
) -> LifetimeResult | None:
    if task.confirmed_lifetime is None:
        try:
            report = await get_task_report(task)
            if report is None:
                return None
            if report.status in {"Error", "Warning"}:
                return task_result(
                    task, "failed",
                    detail="Coiote could not complete all lifetime device operations",
                )
            if report.status != "Success":
                return None
            parameters = await get_parameters(task.coiote_device_id, task.resource)
            matches = [item for item in parameters if item.name == task.resource]
            if len(matches) != 1:
                raise ValueError("Coiote did not return a unique lifetime value")
            parameter = matches[0]
            if parameter.updateTime < report.startTime:
                return None
            if (
                re.fullmatch(r"[0-9]{1,10}", parameter.value) is None
                or not 1 <= int(parameter.value) <= 4294967295
            ):
                raise ValueError("Coiote returned an invalid lifetime")
            task.confirmed_lifetime = int(parameter.value)
            task.observed_at = parameter.updateTime.astimezone(timezone.utc)
            db.commit()
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            raise coiote_request_error(error) from None
        except ValueError:
            raise HTTPException(
                status_code=502, detail="Coiote returned an invalid lifetime result"
            ) from None

    matches_requested = (
        None if task.requested_lifetime is None
        else task.confirmed_lifetime == task.requested_lifetime
    )
    return task_result(
        task, "verification_failed" if matches_requested is False else "succeeded",
        lifetime=task.confirmed_lifetime, matches_requested=matches_requested,
        observed_at=task.observed_at.replace(tzinfo=timezone.utc),
        detail=(
            "Device lifetime does not match the requested value"
            if matches_requested is False else None
        ),
    )


async def wait_for_lifetime(
    device: Device, lifetime: int | None, db: Session
) -> LifetimeResult:
    task = await schedule_lifetime(device, lifetime, db)
    while True:
        result = await get_lifetime_result(task, db)
        if result is not None:
            return result
        await sleep(LIFETIME_POLL_INTERVAL_SECONDS)


async def complete_lifetime_request(
    device: Device, lifetime: int | None, response: Response, db: Session
) -> LifetimeResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await wait_for(
            wait_for_lifetime(device, lifetime, db),
            timeout=LIFETIME_TIMEOUT_SECONDS,
        )
    except AsyncTimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                "Timed out waiting for Coiote to return a fresh device lifetime. "
                "A submitted write may still execute later; read the lifetime "
                "to check before retrying a write."
            ),
            headers={"Cache-Control": "no-store"},
        ) from None
    if result.state == "failed":
        response.status_code = 502
    elif result.state == "verification_failed":
        response.status_code = 409
    return result
