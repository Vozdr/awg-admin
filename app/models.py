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

