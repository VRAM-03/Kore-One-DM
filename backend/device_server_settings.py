"""Allowlisted LwM2M Server settings with persisted, verified device operations."""

import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from sqlalchemy import JSON, Column, DateTime, Integer, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, get_db
from .device_lifetime import discover_server_resource
from .device_operations import (
    DataModelParameter, coiote_request, get_coiote_task_report, get_parameters, registered_device,
)
from .device_sync import coiote_request_error
from .models import Device

router = APIRouter(tags=["Device server settings"])
SettingValue = bool | int | str


class SettingDefinition(BaseModel):
    resource_id: int
    label: str
    data_type: Literal["boolean", "integer", "string"]
    unit: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    warning: str | None = None


SETTING_DEFINITIONS = {
    item.resource_id: item for item in [
        SettingDefinition(
            resource_id=6, label="Notification storing", data_type="boolean",
            warning="Controls whether notifications are stored while the server is disabled or offline.",
        ),
        SettingDefinition(
            resource_id=7, label="Binding", data_type="string",
            warning="Changing transport binding can interrupt connectivity. Use only a binding supported by the device.",
        ),
        *[
            SettingDefinition(
                resource_id=resource_id, label=label, data_type="integer",
                unit=unit, minimum=0, maximum=4294967295,
                warning="Changes registration and communication retry behavior; some settings take effect on the next registration attempt.",
            )
            for resource_id, label, unit in [
                (14, "Initial registration delay timer", "seconds"),
                (17, "Communication retry count", None),
                (18, "Communication retry timer", "seconds"),
                (19, "Communication sequence delay timer", "seconds"),
                (20, "Communication sequence retry count", None),
            ]
        ],
        SettingDefinition(
            resource_id=23, label="Mute Send", data_type="boolean",
            warning="True disables LwM2M Send messages; false enables them. This is not a notification or registration switch.",
        ),
    ]
}

DEVICE_RESOURCES = {
    0: "Manufacturer", 1: "Model Number", 2: "Serial Number", 3: "Firmware Version",
    4: "Reboot", 6: "Available Power Sources",
}


class ResourceMetadata(BaseModel):
    path: str
    resourceId: int = Field(strict=True, ge=0)
    type: str
    operations: list[Literal["Read", "Write", "Execute"]]


class ServerSetting(SettingDefinition):
    resource: str | None = None
    readable: bool = False
    writable: bool = False
    value: SettingValue | None = None
    observed_at: datetime | None = None


class DeviceResourceSetting(BaseModel):
    resource_id: int
    label: str
    readable: bool = False
    executable: bool = False
    observations: list[DataModelParameter] = Field(default_factory=list)


class RebootConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(strict=True)


class SettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: SettingValue

    @field_validator("value", mode="before")
    @classmethod
    def require_scalar(cls, value):
        if type(value) not in (bool, int, str):
            raise ValueError("A boolean, integer or string value is required")
        return value


class ServerSettingTask(Base):
    __tablename__ = "device_server_setting_tasks"

    id = Column(String, primary_key=True)
    local_device_id = Column(Integer, nullable=False, index=True)
    coiote_device_id = Column(String, nullable=False)
    resource_id = Column(Integer, nullable=False)
    resource = Column(String, nullable=False)
    provider_task_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    state = Column(String, nullable=False)
    requested_value = Column(JSON, nullable=True)
    value = Column(JSON, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(String, nullable=True)
    active_device = Column(String, unique=True, nullable=True)


class SettingOperation(BaseModel):
    id: str
    resource_id: int
    resource: str
    kind: Literal["read", "write", "reboot"]
    state: Literal["pending", "submission_unknown", "succeeded", "failed", "verification_failed"]
    requested_value: SettingValue | None = None
    value: SettingValue | None = None
    observed_at: datetime | None = None
    error: str | None = None
    observations: list[DataModelParameter] = Field(default_factory=list)


class ServerSettingsResponse(BaseModel):
    settings: list[ServerSetting]
    unresolved_operation: SettingOperation | None = None
    completed_operation: SettingOperation | None = None


def operation_result(task: ServerSettingTask) -> SettingOperation:
    observations = task.value if isinstance(task.value, list) else []
    return SettingOperation(
        id=task.id, resource_id=task.resource_id, resource=task.resource,
        kind=task.kind, state=task.state, requested_value=task.requested_value,
        value=None if isinstance(task.value, list) else task.value,
        observations=observations, error=task.error,
        observed_at=(
            task.observed_at.replace(tzinfo=timezone.utc) if task.observed_at else None
        ),
    )


def definition_for(resource_id: int) -> SettingDefinition:
    definition = SETTING_DEFINITIONS.get(resource_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Server setting is not supported")
    return definition


def validate_value(definition: SettingDefinition, value: SettingValue) -> SettingValue:
    if definition.data_type == "boolean":
        valid = type(value) is bool
    elif definition.data_type == "integer":
        valid = type(value) is int and definition.minimum <= value <= definition.maximum
    else:
        valid = (
            type(value) is str
            and re.fullmatch(r"(?:UQ|SQ|UQS|[UTSN]{1,4})", value) is not None
            and len(set(value)) == len(value)
        )
    if not valid:
        raise ValueError(f"Invalid value for {definition.label}")
    return value


def parse_value(definition: SettingDefinition, value: str) -> SettingValue:
    if definition.data_type == "boolean":
        if value not in ("true", "false", "1", "0"):
            raise ValueError("Invalid boolean")
        parsed = value in ("true", "1")
    elif definition.data_type == "integer":
        if re.fullmatch(r"[0-9]{1,10}", value) is None:
            raise ValueError("Invalid integer")
        parsed = int(value)
    else:
        parsed = value
    return validate_value(definition, parsed)


async def discover_settings(device: Device) -> list[ServerSetting]:
    try:
        parameters = await get_parameters(device.coiote_device_id, "LwM2M Server.")
        prefix = discover_server_resource(parameters).removesuffix("Lifetime")
        metadata = TypeAdapter(list[ResourceMetadata]).validate_python(
            await coiote_request(
                "GET",
                f"/instantiatedResources/resourcesData/{quote(device.coiote_device_id, safe='')}",
                params={"parameters": "LwM2M Server"},
            )
        )
        selected = {}
        for resource in metadata:
            if (
                resource.resourceId not in SETTING_DEFINITIONS
                or not resource.path.startswith(prefix)
            ):
                continue
            if resource.resourceId in selected:
                raise ValueError("Duplicate server setting")
            selected[resource.resourceId] = resource
        settings = []
        for resource_id, definition in SETTING_DEFINITIONS.items():
            resource = selected.get(resource_id)
            setting = ServerSetting(**definition.model_dump())
            if resource is not None:
                expected_types = {
                    "boolean": {"Boolean"}, "string": {"String"},
                    "integer": {"Integer", "UnsignedInteger", "Unsigned Integer"},
                }
                if resource.type not in expected_types[definition.data_type]:
                    raise ValueError("Unexpected server setting type")
                setting.resource = resource.path
                setting.readable = "Read" in resource.operations
                setting.writable = setting.readable and "Write" in resource.operations
                matches = [p for p in parameters if p.name == resource.path]
                if len(matches) > 1:
                    raise ValueError("Duplicate server value")
                if matches:
                    setting.value = parse_value(definition, matches[0].value)
                    setting.observed_at = matches[0].updateTime
            settings.append(setting)
        return settings
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned invalid server settings") from None


@router.get("/devices/{endpoint_name}/server-settings", response_model=ServerSettingsResponse)
async def list_server_settings(
    endpoint_name: str, response: Response, db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = registered_device(endpoint_name, db)
    active = db.query(ServerSettingTask).filter_by(
        active_device=f"{device.id}:{device.coiote_device_id}",
    ).first()
    operation = await refresh_setting_task(active, db) if active is not None else None
    return ServerSettingsResponse(
        settings=await discover_settings(device),
        unresolved_operation=operation if active is not None and active.active_device else None,
        completed_operation=operation if active is not None and not active.active_device else None,
    )


async def submit_setting(
    endpoint_name: str, resource_id: int, kind: str,
    value: SettingValue | None, response: Response, db: Session,
) -> SettingOperation:
    definition = definition_for(resource_id)
    if kind == "write":
        try:
            validate_value(definition, value)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    device = registered_device(endpoint_name, db)
    active_key = await require_idle(device, db)
    setting = next(s for s in await discover_settings(device) if s.resource_id == resource_id)
    if not setting.resource or not setting.readable or (kind == "write" and not setting.writable):
        raise HTTPException(status_code=409, detail="This setting is not exposed with the required device permissions")
    operations = []
    if kind == "write":
        serialized = str(value).lower() if type(value) is bool else str(value)
        operations.append({"write": {"key": setting.resource, "value": serialized}})
    operations.append({"read": {"key": setting.resource}})
    return await queue_operation(device, active_key, resource_id, setting.resource, kind, value, operations, response, db)


async def require_idle(device: Device, db: Session) -> str:
    active_key = f"{device.id}:{device.coiote_device_id}"
    active = db.query(ServerSettingTask).filter_by(active_device=active_key).first()
    if active is not None:
        await refresh_setting_task(active, db)
    if active is not None and active.active_device:
        raise HTTPException(status_code=409, detail={
            "message": "A configuration operation is unresolved. Check its result before submitting another.",
            "operation_id": active.id,
        })
    return active_key


async def queue_operation(
    device: Device, active_key: str, resource_id: int, resource: str,
    kind: str, value: SettingValue | None, operations: list[dict[str, dict[str, str]]],
    response: Response, db: Session,
) -> SettingOperation:
    task = ServerSettingTask(
        id=uuid4().hex, local_device_id=device.id, coiote_device_id=device.coiote_device_id,
        resource_id=resource_id, resource=resource, kind=kind,
        state="submission_unknown", requested_value=value, active_device=active_key,
        error="Submission has not yet been confirmed. Do not retry.",
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another server setting operation was submitted; reload its result before retrying") from None
    try:
        provider_id = await coiote_request(
            "POST", f"/tasks/configure/{quote(device.coiote_device_id, safe='')}",
            params={"rerunIfExists": "false"},
            json={"taskDefinition": {
                "name": f"nexyn-server-setting-{task.id}", "batchRequests": False,
                "executeImmediately": True, "operations": operations,
            }},
        )
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("Invalid task ID")
        task.provider_task_id = provider_id
        task.state = "pending"
        task.error = None
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as error:
        if isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500:
            task.state = "failed"
            task.active_device = None
            task.error = "The connector rejected the configuration request."
        else:
            task.error = "Submission is unconfirmed. Verify the device state in the connector before retrying."
    db.commit()
    response.status_code = 202 if task.active_device else 200
    response.headers["Cache-Control"] = "no-store"
    return operation_result(task)


def device_resource_key(resource_id: int) -> str:
    return f"Device.0.{DEVICE_RESOURCES[resource_id]}"


def device_observations(
    parameters: list[DataModelParameter], key: str, resource_id: int,
) -> list[DataModelParameter]:
    matches = [
        item for item in parameters
        if (re.fullmatch(re.escape(key) + r"\.[0-9]+", item.name) is not None
            if resource_id == 6 else item.name == key)
    ]
    if len({item.name for item in matches}) != len(matches):
        raise ValueError("Duplicate Device resource observation")
    return matches


async def device_resource_metadata(device: Device) -> dict[int, ResourceMetadata]:
    raw = await coiote_request(
        "GET", f"/instantiatedResources/resourcesData/{quote(device.coiote_device_id, safe='')}",
        params={"parameters": "Device"},
    )
    metadata = {}
    for entry in TypeAdapter(list[ResourceMetadata]).validate_python(raw):
        resource_id = entry.resourceId
        if resource_id in DEVICE_RESOURCES and entry.path == device_resource_key(resource_id):
            if resource_id in metadata:
                raise ValueError("Duplicate Device resource")
            metadata[resource_id] = entry
    return metadata


@router.get("/devices/{endpoint_name}/device-settings", response_model=list[DeviceResourceSetting])
async def list_device_settings(
    endpoint_name: str, response: Response, db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = registered_device(endpoint_name, db)
    try:
        metadata = await device_resource_metadata(device)
        parameters = await get_parameters(device.coiote_device_id, "Device.0.")
        resources = []
        for resource_id, label in DEVICE_RESOURCES.items():
            entry = metadata.get(resource_id)
            readable = resource_id != 4 and entry is not None and "Read" in entry.operations
            executable = resource_id == 4 and entry is not None and "Execute" in entry.operations
            resources.append(DeviceResourceSetting(
                resource_id=resource_id, label=label, readable=readable, executable=executable,
                observations=device_observations(parameters, entry.path, resource_id) if readable and entry else [],
            ))
        return resources
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned invalid Device resources") from None


async def submit_device_resource(
    endpoint_name: str, resource_id: int, reboot: bool, response: Response, db: Session,
) -> SettingOperation:
    if resource_id not in DEVICE_RESOURCES or (resource_id == 4) != reboot:
        raise HTTPException(status_code=404, detail="Device resource operation is not supported")
    device = registered_device(endpoint_name, db)
    active_key = await require_idle(device, db)
    try:
        entry = (await device_resource_metadata(device)).get(resource_id)
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned invalid Device resources") from None
    if entry is None or ("Execute" if reboot else "Read") not in entry.operations:
        raise HTTPException(status_code=409, detail="Device does not expose the required operation")
    return await queue_operation(
        device, active_key, resource_id, entry.path, "reboot" if reboot else "read", None,
        [{"execute" if reboot else "read": {"key": entry.path}}], response, db,
    )


@router.post("/devices/{endpoint_name}/device-settings/{resource_id}/read", response_model=SettingOperation)
async def read_device_setting(
    endpoint_name: str, resource_id: int, response: Response, db: Session = Depends(get_db),
):
    return await submit_device_resource(endpoint_name, resource_id, False, response, db)


@router.post("/devices/{endpoint_name}/device-settings/reboot", response_model=SettingOperation)
async def reboot_device(
    endpoint_name: str, confirmation: RebootConfirmation, response: Response, db: Session = Depends(get_db),
):
    if not confirmation.confirm:
        raise HTTPException(status_code=422, detail="Reboot must be explicitly confirmed")
    return await submit_device_resource(endpoint_name, 4, True, response, db)


@router.post("/devices/{endpoint_name}/server-settings/{resource_id}/read", response_model=SettingOperation)
async def read_server_setting(
    endpoint_name: str, resource_id: int, response: Response,
    db: Session = Depends(get_db),
):
    return await submit_setting(endpoint_name, resource_id, "read", None, response, db)


@router.patch("/devices/{endpoint_name}/server-settings/{resource_id}", response_model=SettingOperation)
async def write_server_setting(
    endpoint_name: str, resource_id: int, update: SettingUpdate, response: Response,
    db: Session = Depends(get_db),
):
    return await submit_setting(endpoint_name, resource_id, "write", update.value, response, db)


@router.get("/devices/{endpoint_name}/server-settings/operations/{operation_id}", response_model=SettingOperation)
async def check_server_setting(
    endpoint_name: str, operation_id: str, response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = registered_device(endpoint_name, db)
    task = db.query(ServerSettingTask).filter_by(
        id=operation_id, local_device_id=device.id, coiote_device_id=device.coiote_device_id,
    ).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Server setting operation not found")
    return await refresh_setting_task(task, db)


async def refresh_setting_task(task: ServerSettingTask, db: Session) -> SettingOperation:
    if task.state != "pending":
        return operation_result(task)
    try:
        report = await get_coiote_task_report(task.provider_task_id, task.coiote_device_id)
        if report is None or report.status in {"NotStarted", "InProgress", "ErrorRetryable"}:
            return operation_result(task)
        if report.status in {"Error", "Warning"}:
            task.state = "failed"
            task.error = "The device could not complete the configuration operation."
        elif task.kind == "reboot":
            task.state = "succeeded"
        elif task.resource.startswith("Device.0."):
            parameters = await get_parameters(task.coiote_device_id, task.resource)
            observations = device_observations(parameters, task.resource, task.resource_id)
            if not observations or any(item.updateTime < report.startTime for item in observations):
                return operation_result(task)
            task.value = [item.model_dump(mode="json") for item in observations]
            task.state = "succeeded"
        else:
            parameters = await get_parameters(task.coiote_device_id, task.resource)
            matches = [p for p in parameters if p.name == task.resource]
            if len(matches) != 1:
                raise ValueError("A unique read-back value was not returned")
            parameter = matches[0]
            if parameter.updateTime < report.startTime:
                return operation_result(task)
            task.value = parse_value(definition_for(task.resource_id), parameter.value)
            task.observed_at = parameter.updateTime
            matches_requested = (
                task.kind == "read"
                or (type(task.value) is type(task.requested_value) and task.value == task.requested_value)
            )
            task.state = "succeeded" if matches_requested else "verification_failed"
            task.error = None if matches_requested else "The device read-back differs from the requested value."
        task.active_device = None
        db.commit()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned an invalid setting verification result; the operation remains unresolved") from None
    return operation_result(task)
