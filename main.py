from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from typing import Optional
from datetime import datetime, timezone
import httpx
import os
import re
from uuid import uuid4

from database import engine, Base, get_db, initialize_database
from device_lifetime import router as lifetime_router
from device_time import router as time_router
from device_sync import coiote_request_error, refresh_devices
from models import Device, Group
from coiote_client import (
    create_coiote_device,
    delete_coiote_device,
    update_coiote_device,
)
from fota_client import (
    create_firmware_resource,
    delete_firmware_resource,
    get_firmware_task_report,
    start_firmware_update,
    upload_firmware_data,
)

initialize_database(engine)

app = FastAPI()
app.include_router(lifetime_router)
app.include_router(time_router)
FOTA_ENABLED = os.getenv("FOTA_ENABLED", "false").strip().lower() == "true"
MAX_FIRMWARE_SIZE_BYTES = 100 * 1024 * 1024


def fota_request_error(
    operation: str, error: httpx.HTTPStatusError | httpx.RequestError
) -> HTTPException:
    if isinstance(error, httpx.HTTPStatusError):
        detail = f"Coiote {operation} failed (HTTP {error.response.status_code})"
    else:
        detail = f"Coiote {operation} could not be completed"
    return HTTPException(status_code=502, detail=detail)


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

@app.get("/devices/{endpoint_name}", response_model=DeviceDetailsResponse)
def get_device(endpoint_name: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = refresh_devices(db)
    device = next(
        (item for item in result.devices if item.endpoint_name == endpoint_name), None
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "local": device,
        "coiote_snapshot": None if device.connection_status == "not_in_coiote" else {
            "fetched_at": device.refreshed_at.replace(tzinfo=timezone.utc),
            "device_id": device.coiote_device_id,
            "endpoint_name": device.endpoint_name,
            "friendly_name": device.friendly_name,
            "connector_type": device.connector_type,
            "domain": device.domain,
            "security_mode": device.security_mode,
        },
    }


@app.get("/devices", response_model=list[DeviceResponse])
def list_devices(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return refresh_devices(db).devices


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


@app.post(
    "/devices/firmware/{endpoint_name}",
    status_code=202,
    summary="Upload firmware and start a single-device update",
    description=(
        "Uploads a firmware image to Coiote and schedules an update for this device. "
        "The device must support the LwM2M Firmware Update object (Object 5). "
        "Firmware updates are disabled unless FOTA_ENABLED=true."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_and_start_firmware_update(
    endpoint_name: str,
    request: Request,
    filename: str = Query(default="firmware.pkg", min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    if not FOTA_ENABLED:
        raise HTTPException(status_code=503, detail="Firmware updates are disabled")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", filename) is None:
        raise HTTPException(status_code=422, detail="Invalid firmware filename")
    if not filename.lower().endswith(".pkg"):
        raise HTTPException(status_code=422, detail="Firmware filename must use the .pkg extension")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        raise HTTPException(status_code=415, detail="Expected application/octet-stream firmware data")
    try:
        content_length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise HTTPException(status_code=411, detail="Content-Length is required") from None
    if content_length <= 0:
        raise HTTPException(status_code=400, detail="Firmware file must not be empty")
    if content_length > MAX_FIRMWARE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Firmware file exceeds the 100 MiB limit")

    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.coiote_device_id:
        raise HTTPException(status_code=409, detail="Device is not registered in Coiote")

    resource_key = uuid4().hex
    resource_name = f"nexyn-firmware-{resource_key}"
    domain = device.domain or "/main/xbrybx34/"
    try:
        resource_id = await create_firmware_resource(
            resource_name, filename, domain
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise fota_request_error("firmware resource creation", error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned an invalid firmware resource") from None

    received_bytes = 0

    async def firmware_stream():
        nonlocal received_bytes
        async for chunk in request.stream():
            received_bytes += len(chunk)
            if received_bytes > MAX_FIRMWARE_SIZE_BYTES:
                raise HTTPException(status_code=413, detail="Firmware file exceeds the 100 MiB limit")
            yield chunk

    try:
        await upload_firmware_data(resource_id, firmware_stream())
        if received_bytes != content_length:
            raise HTTPException(status_code=400, detail="Firmware upload size did not match Content-Length")
    except (httpx.HTTPStatusError, httpx.RequestError, HTTPException) as error:
        try:
            await delete_firmware_resource(resource_id)
        except (httpx.HTTPStatusError, httpx.RequestError):
            pass
        if isinstance(error, HTTPException):
            raise error
        raise fota_request_error("firmware file upload", error) from None

    try:
        task_id = await start_firmware_update(device.coiote_device_id, resource_id)
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise fota_request_error("firmware task scheduling", error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned an invalid firmware task") from None

    return {"task_id": task_id, "state": "accepted"}


@app.get("/devices/firmware/{endpoint_name}/{task_id}")
async def get_firmware_update_status(
    endpoint_name: str, task_id: str, db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.coiote_device_id:
        raise HTTPException(status_code=409, detail="Device is not registered in Coiote")

    try:
        report = await get_firmware_task_report(task_id, device.coiote_device_id)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            try:
                error_body = error.response.json()
            except ValueError:
                error_body = {}
            if isinstance(error_body, dict) and error_body.get("code") == 4041:
                return JSONResponse(
                    content={"task_id": task_id, "state": "pending"},
                    status_code=202,
                )
            if isinstance(error_body, dict) and error_body.get("code") == 4040:
                raise HTTPException(status_code=404, detail="Firmware task not found") from None
        raise coiote_request_error(error) from None
    except httpx.RequestError as error:
        raise coiote_request_error(error) from None
    except ValueError:
        raise HTTPException(status_code=502, detail="Coiote returned an invalid firmware report") from None

    upstream_status = report.get("status")
    status = upstream_status if isinstance(upstream_status, str) else "Unknown"
    normalized_status = status.casefold()
    if normalized_status in {"success", "succeeded"}:
        state = "succeeded"
    elif normalized_status in {"error", "failed", "failure"}:
        state = "failed"
    else:
        state = "in_progress"

    allowed_properties = {
        "newFirmwareVersion": "new_firmware_version",
        "oldFirmwareVersion": "old_firmware_version",
        "result": "result",
        "resultDetails": "result_details",
    }
    firmware_result = {}
    properties = report.get("properties")
    if isinstance(properties, list):
        for item in properties:
            if isinstance(item, dict) and item.get("name") in allowed_properties:
                value = item.get("value")
                if isinstance(value, str):
                    firmware_result[allowed_properties[item["name"]]] = value[:500]

    summary = report.get("summary")
    return {
        "task_id": task_id,
        "state": state,
        "status": status,
        "summary": summary[:500] if isinstance(summary, str) else None,
        "started_at": report.get("startTime"),
        "finished_at": report.get("finishTime"),
        "firmware": firmware_result,
    }