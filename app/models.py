from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, validator


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    comment: str = Field(default="", max_length=256)
    pool: str
    dns: str = ""
    allowed_ips: str = "0.0.0.0/0,::/0"
    keepalive: int = Field(default=30, ge=0, le=65535)
    use_preshared_key: bool = True

    @validator("name")
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("name is empty")
        if any(ch in value for ch in "\r\n\t"):
            raise ValueError("name contains unsupported characters")
        return value

    @validator("comment", "dns", "allowed_ips")
    def single_line(cls, value: str) -> str:
        return " ".join(value.replace("\r", " ").replace("\n", " ").split())


class ClientUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    comment: str = Field(default="", max_length=256)
    dns: str = ""
    allowed_ips: str = "0.0.0.0/0,::/0"
    keepalive: int = Field(default=30, ge=0, le=65535)
    psk_action: Literal["keep", "generate", "remove"] = "keep"

    _clean_name = validator("name", allow_reuse=True)(ClientCreate.clean_name.__func__)
    _single_line = validator("comment", "dns", "allowed_ips", allow_reuse=True)(
        ClientCreate.single_line.__func__
    )


class ClientToggle(BaseModel):
    disabled: bool


class SyncRequest(BaseModel):
    confirm: bool = False

