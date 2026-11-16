"""Read and update optional LwM2M Device time resources through Coiote."""

import re
from asyncio import TimeoutError as AsyncTimeoutError, sleep, wait_for
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from database import get_db
from device_operations import (
    coiote_request,
    get_coiote_task_report,
    get_parameters,
    registered_device,
)
from device_sync import coiote_request_error

router = APIRouter(tags=["Device time"])
TIME_TIMEOUT_SECONDS = 30.0
TIME_POLL_INTERVAL_SECONDS = 1.0
TimeField = Literal["timezone", "utc_offset", "current_time"]
TIME_RESOURCES: dict[TimeField, str] = {
    "timezone": "Device.0.Timezone",
    "utc_offset": "Device.0.UTC Offset",
    "current_time": "Device.0.Current Time",
}


class DeviceTimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = Field(
        default=None, strict=True, min_length=1, max_length=255,
        description="IANA timezone, e.g. Asia/Kolkata. Omit to leave unchanged.",
    )
    utc_offset: str | None = Field(
        default=None, strict=True, pattern=r"^[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]$",
        description="Signed ISO 8601 UTC offset, e.g. +05:30. Omit to leave unchanged.",
    )
    current_time: AwareDatetime | None = Field(
        default=None,
        description=(
            "ISO 8601 timestamp with timezone and whole-second precision, "
            "e.g. 2026-11-16T17:50:00+05:30. Omit to leave unchanged."
        ),
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("timezone cannot be null; omit it to leave unchanged")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            if isinstance(error, ZoneInfoNotFoundError):
                try:
                    ZoneInfo("UTC")
                except ZoneInfoNotFoundError:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Server timezone data is unavailable. Install tzdata "
                            "in the Python environment running this backend."
                        ),
                    ) from None
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value

    @field_validator("utc_offset")
    @classmethod
    def validate_utc_offset(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("utc_offset cannot be null; omit it to leave unchanged")
        return value

    @field_validator("current_time", mode="before")
    @classmethod
    def validate_current_time_format(cls, value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:Z|[+-][0-9]{2}:[0-9]{2})",
            value,
        ) is None:
            raise ValueError(
                "current_time must be an ISO 8601 timestamp with timezone "
                "and whole-second precision"
            )
        return value

    @field_validator("current_time")
    @classmethod
    def normalize_current_time(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class DeviceTimeValues(BaseModel):
    timezone: str | None = None
    utc_offset: str | None = None
    current_time: AwareDatetime | None = None


class DeviceTimeResult(BaseModel):
    endpoint_name: str
    device_id: str
    task_id: str
    operation: Literal["read", "write"]
    state: Literal["succeeded", "failed", "verification_failed"]
    requested: dict[TimeField, str]
    values: DeviceTimeValues | None = None
    observed_at: dict[TimeField, datetime] | None = None
    detail: str | None = None


async def perform_time_operation(
    endpoint_name: str, device_id: str, update: DeviceTimeUpdate
) -> DeviceTimeResult:
    requested: dict[TimeField, str] = {}
    for field in TIME_RESOURCES:
        value = getattr(update, field)
        if value is not None:
            requested[field] = (
                value.isoformat().replace("+00:00", "Z")
                if isinstance(value, datetime) else value
            )
    fields = list(requested) if requested else list(TIME_RESOURCES)
    operations: list[dict[str, dict[str, str]]] = [
        {"write": {"key": TIME_RESOURCES[field], "value": value}}
        for field, value in requested.items()
    ]
    operations.extend({"read": {"key": TIME_RESOURCES[field]}} for field in fields)
    task_id = await coiote_request(
        "POST", f"/tasks/configure/{quote(device_id, safe='')}",
        params={"rerunIfExists": "false"},
        json={"taskDefinition": {
            "name": f"nexyn-device-time-{uuid4().hex}",
            "batchRequests": False,
            "executeImmediately": True,
            "operations": operations,
        }},
    )
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Coiote returned an invalid task identifier")
    result = DeviceTimeResult(
        endpoint_name=endpoint_name, device_id=device_id, task_id=task_id,
        operation="write" if requested else "read",
        state="succeeded", requested=requested,
    )
    while True:
        report = await get_coiote_task_report(task_id, device_id)
        if report is None or report.status in {"NotStarted", "InProgress", "ErrorRetryable"}:
            await sleep(TIME_POLL_INTERVAL_SECONDS)
            continue
        if report.status in {"Error", "Warning"}:
            result.state = "failed"
            result.detail = (
                "Coiote could not complete all device time operations. "
                "Earlier writes may have succeeded. Send an empty body to read "
                "the current settings before retrying."
            )
            return result
        parameters = await get_parameters(device_id, "Device.0.")
        values: dict[TimeField, str] = {}
        observed_at: dict[TimeField, datetime] = {}
        for field in fields:
            matches = [
                parameter for parameter in parameters
                if parameter.name == TIME_RESOURCES[field]
            ]
            if len(matches) != 1:
                raise ValueError("Coiote did not return a unique time resource")
            parameter = matches[0]
            values[field] = parameter.value
            observed_at[field] = parameter.updateTime.astimezone(timezone.utc)
        if any(timestamp < report.startTime for timestamp in observed_at.values()):
            await sleep(TIME_POLL_INTERVAL_SECONDS)
            continue
        result.values = DeviceTimeValues.model_validate(values)
        result.observed_at = observed_at
        # Current Time advances on the device; a successful write and fresh
        # readback confirm completion without requiring a frozen clock value.
        if any(
            values[field] != expected for field, expected in requested.items()
            if field != "current_time"
        ):
            result.state = "verification_failed"
            result.detail = (
                "The device-reported timezone or UTC offset differs from "
                "the requested value."
            )
        return result


@router.patch(
    "/devices/{endpoint_name}/time",
    response_model=DeviceTimeResult,
    response_model_exclude_none=True,
    summary="Change device timezone, UTC offset or current time through Coiote",
    description=(
        "Supply any combination of timezone, utc_offset and current_time. "
        "Omitted settings are not written. An empty body reads all three. "
        "Coiote execution and fresh readback are handled internally in one "
        "application request, with a 30-second deadline. Device resources "
        "/3/0/15, /3/0/14 and /3/0/13 must support the requested operations."
    ),
)
async def update_device_time(
    endpoint_name: str,
    response: Response,
    update: Annotated[DeviceTimeUpdate, Body(default_factory=DeviceTimeUpdate)],
    db: Session = Depends(get_db),
):
    device = registered_device(endpoint_name, db)
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await wait_for(
            perform_time_operation(endpoint_name, device.coiote_device_id, update),
            timeout=TIME_TIMEOUT_SECONDS,
        )
    except AsyncTimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                "Timed out waiting for Coiote to return fresh device time settings. "
                "Submitted writes may still execute later. Send an empty body "
                "to read the current settings before retrying a write."
            ),
            headers={"Cache-Control": "no-store"},
        ) from None
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(
            status_code=502, detail="Coiote returned an invalid device time response"
        ) from None
    if result.state == "failed":
        response.status_code = 502
    elif result.state == "verification_failed":
        response.status_code = 409
    return result
