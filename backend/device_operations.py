"""Shared Coiote device-operation transport and response validation."""

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .coiote_client import COIOTE_BASE_URL, get_coiote_token
from .models import Device


class DataModelParameter(BaseModel):
    name: str
    value: str
    updateTime: datetime

    @field_validator("updateTime")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Coiote timestamps must include a timezone")
        return value


class TaskReport(BaseModel):
    taskId: str = Field(strict=True)
    deviceId: str = Field(strict=True)
    status: Literal[
        "InProgress", "Success", "Warning", "Error", "NotStarted", "ErrorRetryable"
    ]
    startTime: datetime

    @field_validator("startTime")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Coiote timestamps must include a timezone")
        return value


async def coiote_request(method: str, path: str, **kwargs: Any) -> Any:
    token = await run_in_threadpool(get_coiote_token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            f"{COIOTE_BASE_URL}/api/coiotedm/v3{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
    response.raise_for_status()
    return response.json()


async def get_parameters(device_id: str, resource: str) -> list[DataModelParameter]:
    payload = await coiote_request(
        "GET", f"/cachedDataModels/{quote(device_id, safe='')}",
        params={"parameters": resource},
    )
    return TypeAdapter(list[DataModelParameter]).validate_python(payload)


async def get_coiote_task_report(task_id: str, device_id: str) -> TaskReport | None:
    try:
        payload = await coiote_request(
            "GET",
            f"/taskReports/{quote(task_id, safe='')}/{quote(device_id, safe='')}",
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            try:
                body = error.response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("code") == 4041:
                return None
            if isinstance(body, dict) and body.get("code") == 4040:
                raise HTTPException(
                    status_code=404, detail="Coiote task not found"
                ) from None
        raise
    report = TaskReport.model_validate(payload)
    if report.taskId != task_id or report.deviceId != device_id:
        raise ValueError("Inconsistent Coiote task report")
    return report


def registered_device(endpoint_name: str, db: Session) -> Device:
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.coiote_device_id:
        raise HTTPException(status_code=409, detail="Device is not registered in Coiote")
    return device
