from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import (
    BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, ValidationInfo,
    field_validator,
)
from typing import Literal, Optional
from datetime import datetime, timezone
import httpx
import re
from uuid import uuid4
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database import engine, SessionLocal, Base, initialize_database
from device_sync import coiote_request_error, refresh_devices
from models import Device, DeviceOperation, Group
from coiote_client import (
    create_coiote_device,
    delete_coiote_device,
    get_coiote_device_resources,
    get_coiote_task_report,
    submit_coiote_read,
    submit_coiote_timezone_write,
    update_coiote_device,
)

initialize_database(engine)

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: item[key] for key in ("type", "loc", "msg")}
                for item in error.errors()
            ]
        },
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DeviceCreate(BaseModel):
    endpoint_name: str
    friendly_name: Optional[str] = None
    security_mode: str = "psk"
    psk_identity: Optional[str] = Field(default=None, validate_default=True, repr=False)
    psk_key: Optional[str] = Field(default=None, validate_default=True, repr=False)

    @field_validator("endpoint_name")
    @classmethod
    def normalize_endpoint_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("endpoint_name must not be blank")
        return normalized

    @field_validator("psk_identity")
    @classmethod
    def validate_psk_identity(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if info.data.get("security_mode") == "psk":
            if value is None or not value.strip():
                raise ValueError("psk_identity must not be blank in PSK mode")
        return value

    @field_validator("psk_key")
    @classmethod
    def validate_psk_key(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if info.data.get("security_mode") == "psk":
            if value is None or not value:
                raise ValueError("psk_key is required in PSK mode")
            if re.fullmatch(r"[0-9a-fA-F]+", value) is None or len(value) % 2:
                raise ValueError(
                    "psk_key must contain an even number of hexadecimal characters "
                    "(0-9, a-f, A-F), with no spaces or 0x prefix"
                )
        return value


class DeviceUpdate(BaseModel):
    friendly_name: str = Field(min_length=1)


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    endpoint_name: str
    friendly_name: Optional[str] = None
    security_mode: Optional[str] = None
    coiote_device_id: Optional[str] = None
    onboarding_status: Optional[str] = None
    connection_status: Optional[str] = None
    refreshed_at: Optional[datetime] = None
    first_registered_at: Optional[datetime] = None
    last_registered_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @field_validator(
        "refreshed_at", "first_registered_at", "last_registered_at",
        "last_seen_at", "created_at"
    )
    @classmethod
    def restore_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class CoioteDeviceSnapshot(BaseModel):
    fetched_at: datetime
    device_id: Optional[str] = None
    endpoint_name: Optional[str] = None
    friendly_name: Optional[str] = None
    connector_type: Optional[str] = None
    domain: Optional[str] = None
    security_mode: Optional[str] = None


class DeviceDetailsResponse(BaseModel):
    local: DeviceResponse
    coiote_snapshot: Optional[CoioteDeviceSnapshot] = None


class CoioteResourceMetadata(BaseModel):
    model_config = ConfigDict(strict=True)

    path: str = Field(min_length=1)
    resourceId: int = Field(ge=0)
    operations: list[Literal["Read", "Write", "Execute"]]
    type: Literal[
        "String", "Integer", "UnsignedInteger", "Float", "Boolean",
        "Opaque", "Time", "Objlnk", "Corelnk", "None",
    ]


class DeviceResourceResponse(BaseModel):
    key: str
    resource_id: int
    data_type: str
    operations: list[Literal["Read", "Write", "Execute"]]


class DeviceResourcesResponse(BaseModel):
    endpoint_name: str
    source: Literal["coiote_stored_model"] = "coiote_stored_model"
    fetched_at: datetime
    resources: list[DeviceResourceResponse]


class DeviceReadRequest(BaseModel):
    resource_key: Literal[
        "Device.0.Manufacturer", "Device.0.Model Number", "Device.0.Firmware Version",
        "Device.0.Timezone",
    ]


class DeviceWriteRequest(BaseModel):
    resource_key: Literal["Device.0.Timezone"]
    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-8")) > 31:
            raise ValueError("Timezone must fit the client's 31-byte setting")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone, such as Etc/UTC") from None
        return value


class DeviceOperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_key: str
    kind: Literal["read", "write"]
    requested_value: Optional[str]
    status: str
    value: Optional[str]
    error: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    @field_validator("created_at", "completed_at")
    @classmethod
    def restore_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class CoioteReadProperty(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    value: str


class CoioteReadReport(BaseModel):
    model_config = ConfigDict(strict=True)

    taskId: str
    deviceId: str
    status: Literal[
        "InProgress", "Success", "Warning", "Error", "NotStarted", "ErrorRetryable",
    ]
    finishTime: Optional[str] = None
    properties: list[CoioteReadProperty]


class GroupCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/groups", response_model=GroupResponse, status_code=201)
def create_group(group: GroupCreate, db: Session = Depends(get_db)):
    new_group = Group(name=group.name)
    db.add(new_group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(Group).filter(Group.name == group.name).first() is not None:
            raise HTTPException(status_code=409, detail="Group name already exists")
        raise
    db.refresh(new_group)
    return new_group


@app.get("/groups", response_model=list[GroupResponse])
def list_groups(db: Session = Depends(get_db)):
    return db.query(Group).order_by(Group.id).all()


@app.post("/devices", response_model=DeviceResponse)
def create_device(device: DeviceCreate, db: Session = Depends(get_db)):
    new_device = Device(
        endpoint_name=device.endpoint_name,
        friendly_name=device.friendly_name,
        security_mode=device.security_mode,
        psk_identity=device.psk_identity,
        psk_key=device.psk_key,
        onboarding_status="created",
    )
    db.add(new_device)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="endpoint_name already exists")
    db.refresh(new_device)

    try:
        coiote_response = create_coiote_device(
            endpoint_name=device.endpoint_name,
            friendly_name=device.friendly_name,
            security_mode=device.security_mode,
            psk_identity=device.psk_identity,
            psk_key=device.psk_key,
        )
        new_device.coiote_device_id = coiote_response.get("id")
        new_device.onboarding_status = "coiote_record_created"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        new_device.onboarding_status = "coiote_registration_failed"
        db.commit()
        error = coiote_request_error(e)
        error.detail = f"Saved locally, but {error.detail}"
        raise error

    db.commit()
    db.refresh(new_device)
    return new_device

@app.post("/sync-devices")
def sync_devices(db: Session = Depends(get_db)):
    result = refresh_devices(db)
    return {
        "synced": result.synced,
        "updated": result.updated,
        "missing_from_coiote": result.missing_from_coiote,
    }

@app.get(
    "/devices/{endpoint_name}", response_model=DeviceDetailsResponse,
    summary="Get stored device details without refreshing Coiote",
)
def get_device(endpoint_name: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "local": device,
        "coiote_snapshot": None if (
            device.refreshed_at is None or not device.coiote_device_id
            or device.connection_status == "not_in_coiote"
        ) else {
            "fetched_at": device.refreshed_at.replace(tzinfo=timezone.utc),
            "device_id": device.coiote_device_id,
            "endpoint_name": device.endpoint_name,
            "friendly_name": device.friendly_name,
            "connector_type": device.connector_type,
            "domain": device.domain,
            "security_mode": device.security_mode,
        },
    }


@app.get(
    "/devices/{endpoint_name}/resources",
    response_model=DeviceResourcesResponse,
    summary="Discover selected safe resources from Coiote's stored model (not a live Read)",
)
def list_device_resources(
    endpoint_name: str, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.coiote_device_id:
        raise HTTPException(status_code=409, detail="Device has no Coiote device ID")

    return DeviceResourcesResponse(
        endpoint_name=device.endpoint_name, fetched_at=datetime.now(timezone.utc),
        resources=fetch_safe_resources(device.coiote_device_id),
    )


def fetch_safe_resources(device_id: str) -> list[DeviceResourceResponse]:
    try:
        records = TypeAdapter(list[CoioteResourceMetadata]).validate_python(
            get_coiote_device_resources(device_id), strict=True
        )
        resources = []
        seen = set()
        safe_ids = {"Manufacturer": 0, "Model Number": 1, "Firmware Version": 3, "Timezone": 15}
        for record in records:
            match = re.fullmatch(
                r"Device\.(0|[1-9][0-9]*)\.(Manufacturer|Model Number|Firmware Version|Timezone)",
                record.path,
            )
            if match is None:
                continue
            if record.resourceId != safe_ids[match[2]] or record.path in seen:
                raise ValueError("Inconsistent resource identifiers")
            if match[2] == "Timezone" and record.type != "String":
                raise ValueError("Timezone must be a string resource")
            seen.add(record.path)
            resources.append(DeviceResourceResponse(
                key=record.path, resource_id=record.resourceId,
                data_type=record.type, operations=record.operations,
            ))
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            status_code=502, detail="Coiote returned an invalid resource response"
        ) from None
    return sorted(resources, key=lambda resource: resource.key)


def find_operation_device(endpoint_name: str, db: Session):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.coiote_device_id:
        raise HTTPException(status_code=409, detail="Device has no Coiote device ID")
    return device


@app.post(
    "/devices/{endpoint_name}/reads",
    response_model=DeviceOperationResponse, status_code=202,
    summary="Submit one live device Read (check the operation endpoint for its result)",
)
def submit_device_read(
    endpoint_name: str, body: DeviceReadRequest, response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = find_operation_device(endpoint_name, db)
    resources = fetch_safe_resources(device.coiote_device_id)
    if not any(r.key == body.resource_key and "Read" in r.operations for r in resources):
        raise HTTPException(status_code=422, detail="Resource is not listed as readable")
    return submit_device_operation(device, body.resource_key, response, db)


@app.post(
    "/devices/{endpoint_name}/writes",
    response_model=DeviceOperationResponse, status_code=202,
    summary="Apply a timezone setting and verify it with a device Read-back",
)
def submit_device_write(
    endpoint_name: str, body: DeviceWriteRequest, response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = find_operation_device(endpoint_name, db)
    existing = db.query(DeviceOperation).filter(
        DeviceOperation.device_id == device.id,
        DeviceOperation.coiote_device_id == device.coiote_device_id,
        DeviceOperation.resource_key == body.resource_key,
        DeviceOperation.kind == "write",
        DeviceOperation.status.in_(("submitting", "pending", "submission_unknown")),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail={
            "message": "A timezone Write is unresolved; check its result before submitting another",
            "operation_id": existing.id,
        })
    resources = fetch_safe_resources(device.coiote_device_id)
    if not any(
        r.key == body.resource_key and "Read" in r.operations and "Write" in r.operations
        for r in resources
    ):
        raise HTTPException(status_code=422, detail="Timezone is not listed as readable and writable")
    return submit_device_operation(device, body.resource_key, response, db, body.value)


def submit_device_operation(
    device: Device, resource_key: str, response: Response, db: Session,
    requested_value: Optional[str] = None,
):
    operation = DeviceOperation(
        id=str(uuid4()), device_id=device.id, coiote_device_id=device.coiote_device_id,
        resource_key=resource_key, status="submitting",
        kind="write" if requested_value is not None else "read",
        requested_value=requested_value,
        error="Submission started; no confirmed Coiote task ID yet",
        created_at=datetime.now(timezone.utc),
    )
    db.add(operation)
    db.commit()
    try:
        if requested_value is None:
            task_id = submit_coiote_read(
                operation.coiote_device_id, operation.resource_key, operation.id
            )
        else:
            task_id = submit_coiote_timezone_write(
                operation.coiote_device_id, operation.resource_key, requested_value, operation.id
            )
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Invalid task identity")
    except (httpx.HTTPError, ValueError, TypeError) as error:
        operation.status = "submission_unknown"
        operation.error = "Submission outcome could not be confirmed; do not automatically resubmit"
        if isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500:
            operation.status = "rejected"
            operation.error = f"Coiote rejected the request (HTTP {error.response.status_code})"
        db.commit()
        raise HTTPException(status_code=502, detail={
            "message": operation.error, "operation_id": operation.id,
        }) from None
    operation.task_id = task_id
    operation.status = "pending"
    operation.error = None
    db.commit()
    db.refresh(operation)
    response.headers["Location"] = (
        f"/devices/{quote(device.endpoint_name, safe='')}/operations/{operation.id}"
    )
    return operation


@app.get(
    "/devices/{endpoint_name}/operations/{operation_id}",
    response_model=DeviceOperationResponse,
)
def get_device_operation(
    endpoint_name: str, operation_id: str, response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    device = find_operation_device(endpoint_name, db)
    operation = db.get(DeviceOperation, operation_id)
    if (operation is None or operation.device_id != device.id
            or operation.coiote_device_id != device.coiote_device_id):
        raise HTTPException(status_code=404, detail="Operation not found for this device")
    if operation.task_id is None or operation.status in ("succeeded", "failed", "not_found"):
        return operation
    try:
        report = CoioteReadReport.model_validate(
            get_coiote_task_report(operation.task_id, operation.coiote_device_id)
        )
        if report.taskId != operation.task_id or report.deviceId != operation.coiote_device_id:
            raise ValueError("Report identity mismatch")
        status = "pending"
        value = None
        error_message = None
        completed_at = None
        if report.status in ("Success", "Warning", "Error"):
            if not report.finishTime:
                raise ValueError("Missing completion time")
            completed_at = datetime.fromisoformat(report.finishTime.replace("Z", "+00:00"))
            if completed_at.utcoffset() is None:
                raise ValueError("Completion time has no timezone")
            completed_at = completed_at.astimezone(timezone.utc)
            if report.status == "Success":
                values = [p.value for p in report.properties if p.name == operation.resource_key]
                if len(values) != 1:
                    raise ValueError("Missing or duplicate read value")
                status, value = "succeeded", values[0]
                if operation.kind == "write" and value != operation.requested_value:
                    status = "failed"
                    error_message = "Timezone Read-back did not match the requested value"
            else:
                action = "Write/verification" if operation.kind == "write" else "Read"
                status, error_message = "failed", f"Coiote reported an unsuccessful {action}"
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise coiote_request_error(error) from None
        try:
            payload = error.response.json()
        except ValueError:
            raise coiote_request_error(error) from None
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == 4041:
            return operation
        if code != 4040:
            raise coiote_request_error(error) from None
        operation.status = "not_found"
        operation.error = "Coiote task was not found"
        db.commit()
        return operation
    except httpx.RequestError as error:
        raise coiote_request_error(error) from None
    except (ValidationError, ValueError, TypeError):
        action = "Write" if operation.kind == "write" else "Read"
        raise HTTPException(status_code=502, detail=f"Coiote returned an invalid {action} report") from None
    operation.status = status
    operation.value = value
    operation.error = error_message
    operation.completed_at = completed_at
    db.commit()
    db.refresh(operation)
    return operation


@app.get(
    "/devices", response_model=list[DeviceResponse],
    summary="List stored devices without refreshing Coiote",
)
def list_devices(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return db.query(Device).order_by(Device.id).all()


@app.patch("/devices/{endpoint_name}", response_model=DeviceResponse)
def update_device(
    endpoint_name: str,
    update: DeviceUpdate,
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.coiote_device_id:
        try:
            update_coiote_device(device.coiote_device_id, update.friendly_name)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise coiote_request_error(e)

    device.friendly_name = update.friendly_name
    db.commit()
    db.refresh(device)
    return device


@app.delete("/devices/{endpoint_name}", status_code=204)
def delete_device(endpoint_name: str, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.coiote_device_id:
        try:
            delete_coiote_device(device.coiote_device_id)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise coiote_request_error(e)

    db.delete(device)
    db.commit()
    return Response(status_code=204)