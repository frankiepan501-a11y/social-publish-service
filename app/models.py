from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    account_config: dict[str, Any] | None = None
    recent_records: list[dict[str, Any]] = Field(default_factory=list)
    now: str | None = None


class PublishScanRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    commit: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False
    limit: int = Field(default=10, ge=1, le=50)
    now: str | None = None


class GenerateBriefRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False


class GenerateScanRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ImageTaskRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    write_task: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False


class ImageTaskScanRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    write_task: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ImageResultIngestRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    image_task_record_id: str | None = None
    image_task_record: dict[str, Any] | None = None
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"


class ImageResultIngestScanRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "auto"
    force: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ReplayRequest(BaseModel):
    run_id: str
    record_id: str | None = None
    record: dict[str, Any] | None = None
    mode: Literal["dry-run", "commit"] = "dry-run"


class InsightsPollRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    window: Literal["24h", "7d", "30d"] = "24h"
