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


class PlanWeeklyRequest(BaseModel):
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class PlanDailyConfirmRequest(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    target_date: str | None = None
    now: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class PlanReselectRequest(BaseModel):
    candidate_record_id: str | None = None
    candidate: dict[str, Any] | None = None
    references: list[dict[str, Any]] = Field(default_factory=list)
    action: Literal["confirm_generate", "reselect_topic", "reselect_reference", "skip_or_reschedule"]
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    reschedule_date: str | None = None


class WeeklyInputCardRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    product_index: list[dict[str, Any]] = Field(default_factory=list)
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None


class WeeklyInputActionRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    product_index: list[dict[str, Any]] = Field(default_factory=list)
    submissions: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None


class ProductIndexSyncRequest(BaseModel):
    product_records: list[dict[str, Any]] = Field(default_factory=list)
    brand: Literal["FUNLAB", "Powkong", "POWKONG", "all"] = "all"
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    limit: int = Field(default=200, ge=1, le=500)


class ReferenceWeeklyDiscoveryRequest(BaseModel):
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None
    limit_per_brand: int = Field(default=6, ge=1, le=20)


class KolWeeklyDiscoveryRequest(BaseModel):
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    existing_candidates: list[dict[str, Any]] = Field(default_factory=list)
    visual_posts: list[dict[str, Any]] = Field(default_factory=list)
    prepare_image_keys: bool = False
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None
    per_brand: int = Field(default=5, ge=1, le=10)
    min_visual_score: int = Field(default=70, ge=0, le=100)


class KolVisualPostDiscoveryRequest(BaseModel):
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    posts: list[dict[str, Any]] = Field(default_factory=list)
    existing_candidates: list[dict[str, Any]] = Field(default_factory=list)
    prepare_image_keys: bool = False
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None
    per_brand: int = Field(default=5, ge=1, le=10)
    min_score: int = Field(default=70, ge=0, le=100)


class KolActionRequest(BaseModel):
    candidate_record_id: str | None = None
    candidate: dict[str, Any] | None = None
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    existing_candidates: list[dict[str, Any]] = Field(default_factory=list)
    action: Literal["approve", "reject_replace", "hold", "block_similar"]
    replacement_count: int = Field(default=1, ge=1, le=5)
    write_back: bool = False
    source: Literal["auto", "manual", "replay"] = "manual"
    week_start: str | None = None
    now: str | None = None


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


class ApprovalCardPreviewRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None


class ApprovalActionRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    action: Literal["approve_schedule", "regenerate_image", "regenerate_copy", "regenerate_both", "reject"]
    write_back: bool = False
    create_image_task: bool = False
    feedback_text: str = ""
    copy_overrides: dict[str, str] = Field(default_factory=dict)
    feedback_dimensions: dict[str, str] = Field(default_factory=dict)
    feedback_tags: list[str] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list)
    change: list[dict[str, str]] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class ReplayRequest(BaseModel):
    run_id: str
    record_id: str | None = None
    record: dict[str, Any] | None = None
    mode: Literal["dry-run", "commit"] = "dry-run"


class InsightsPollRequest(BaseModel):
    record_id: str | None = None
    record: dict[str, Any] | None = None
    window: Literal["24h", "7d", "30d"] = "24h"
