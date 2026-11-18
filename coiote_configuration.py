from hashlib import pbkdf2_hmac
from secrets import token_bytes

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from database import Base, get_db

router = APIRouter(tags=["Connector configuration"])
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class CoioteCredentialHashes(Base):
    __tablename__ = "coiote_credential_hashes"

    id = Column(Integer, primary_key=True)
    username_hash = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)


class CoioteCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: SecretStr = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


def hash_credential(value: str) -> str:
    salt = token_bytes(16)
    iterations = 600_000
    digest = pbkdf2_hmac("sha256", value.encode("utf-8"), salt, iterations)
    return f"pbkdf2-sha256${iterations}${salt.hex()}${digest.hex()}"


def require_local_origin(request: Request) -> None:
    if (
        request.client is None
        or request.client.host not in LOCAL_HOSTS
        or request.url.hostname not in LOCAL_HOSTS
        or request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
    ):
        raise HTTPException(status_code=403, detail="Configuration is available only on the local console")


@router.put(
    "/connectors/coiote/configuration",
    status_code=204,
    dependencies=[Depends(require_local_origin)],
)
def save_coiote_configuration(
    credentials: CoioteCredentials, db: Session = Depends(get_db),
) -> Response:
    username = credentials.username.get_secret_value().strip()
    if not username:
        raise HTTPException(status_code=422, detail="Enter a username or email address")

    hashes = {
        "username_hash": hash_credential(username),
        "password_hash": hash_credential(credentials.password.get_secret_value()),
    }
    db.execute(
        insert(CoioteCredentialHashes).values(id=1, **hashes)
        .on_conflict_do_update(index_elements=["id"], set_=hashes)
    )
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
