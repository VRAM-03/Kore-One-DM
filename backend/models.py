from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .database import Base


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)


class DeviceOperation(Base):
    __tablename__ = "device_operations"

    id = Column(String, primary_key=True)
    device_id = Column(Integer, nullable=False, index=True)
    coiote_device_id = Column(String, nullable=False)
    resource_key = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="read")
    requested_value = Column(String, nullable=True)
    task_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    value = Column(String, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    endpoint_name = Column(String, unique=True, index=True)
    friendly_name = Column(String)
    security_mode = Column(String, default="psk")
    psk_identity = Column(String, nullable=True)
    psk_key = Column(String, nullable=True)
    coiote_device_id = Column(String, nullable=True)
    onboarding_status = Column("status", String, default="created")
    connection_status = Column(String, nullable=True)
    refreshed_at = Column(DateTime(timezone=True), nullable=True)
    first_registered_at = Column(DateTime(timezone=True), nullable=True)
    last_registered_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    connector_type = Column(String, nullable=True)
    domain = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())