from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, validator


class ClientCreate(BaseModel):
    name: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=256)
    pool: str
    address_mode: Literal["next", "manual"] = "next"
    address: str = ""
    dns: str = ""
    allowed_ips: str = "0.0.0.0/0,::/0"
    keepalive: int = Field(default=30, ge=0, le=65535)
    use_preshared_key: bool = True
    preshared_key: str = ""
    public_key: str
    store_private_key: bool = True
    private_key: str = ""

    @validator("name")
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if any(ch in value for ch in "\r\n\t"):
            raise ValueError("name contains unsupported characters")
        return value

    @validator("comment", "dns", "allowed_ips", "address", "public_key", "private_key", "preshared_key")
    def single_line(cls, value: str) -> str:
        return " ".join(value.replace("\r", " ").replace("\n", " ").split())


class ClientUpdate(BaseModel):
    name: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=256)
    address_mode: Literal["keep", "next", "manual"] = "keep"
    pool: str = ""
    address: str = ""
    dns: str = ""
    allowed_ips: str = "0.0.0.0/0,::/0"
    keepalive: int = Field(default=30, ge=0, le=65535)
    use_preshared_key: bool = True
    preshared_key: str = ""
    public_key: str
    store_private_key: bool = True
    private_key: str = ""

    _clean_name = validator("name", allow_reuse=True)(ClientCreate.clean_name.__func__)
    _single_line = validator(
        "comment", "dns", "allowed_ips", "address", "pool", "public_key", "private_key", "preshared_key", allow_reuse=True
    )(
        ClientCreate.single_line.__func__
    )


class ClientToggle(BaseModel):
    disabled: bool


class SyncRequest(BaseModel):
    confirm: bool = False


class EnvVariableValidation(BaseModel):
    envlist: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=128)

    @validator("envlist", "key")
    def clean_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch in value for ch in "\r\n\t,"):
            raise ValueError("unsupported identifier")
        return value


class EnvVariableChange(BaseModel):
    action: Literal["add", "update", "delete"]
    id: str = ""
    envlist: str = Field(default="", max_length=128)
    key: str = Field(default="", max_length=128)
    value: str = Field(default="", max_length=8192)
    comment: str = Field(default="", max_length=512)
    disabled: bool = False

    _clean_envlist = validator("envlist", allow_reuse=True)(EnvVariableValidation.clean_identifier.__func__)
    _clean_key = validator("key", allow_reuse=True)(EnvVariableValidation.clean_identifier.__func__)

    @validator("value", "comment")
    def clean_text(cls, value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ").strip()


class InterfaceAddressChange(BaseModel):
    action: Literal["add", "update", "delete"]
    id: str = ""
    address: str = Field(default="", max_length=64)


class ServerSettingsUpdate(BaseModel):
    revision: str = Field(min_length=16, max_length=128)
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    env_changes: list[EnvVariableChange] = Field(default_factory=list)
    address_changes: list[InterfaceAddressChange] = Field(default_factory=list)
    regenerate_server_key: bool = False

