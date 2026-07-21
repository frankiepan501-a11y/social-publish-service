from __future__ import annotations

from typing import Any

from .models import PublishRequest, SocialCrmP1PublishRequest
from .rules import (
    PLATFORM_FACEBOOK,
    PLATFORM_INSTAGRAM,
    SLOT_FB_PAGE,
    SLOT_IG_CAROUSEL,
    SLOT_IG_FEED,
    bool_value,
    parse_file_tokens,
    parse_urls,
    select_value,
    text_value,
)


ALLOWED_PLATFORMS = {PLATFORM_FACEBOOK, PLATFORM_INSTAGRAM}
PASS_VALUES = {"通过", "已通过", "approved", "approve", "pass", "yes", "true", "ok"}
READY_DRAFT_VALUES = {"待发布", "已排期", "排期中", "已审批", "publish-ready", "scheduled", "ready"}
FINAL_ASSET_FIELDS = ("最终素材确认", "素材确认", "图片确认", "最终图片确认", "素材审核结果")


def _first_value(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = fields.get(name)
        if value not in (None, "", []):
            return value
    return None


def _passed(value: Any) -> bool:
    if bool_value(value):
        return True
    raw = select_value(value).strip().lower()
    return raw in PASS_VALUES


def normalize_platform(value: Any) -> str:
    raw = select_value(value).strip()
    lowered = raw.lower()
    if lowered in {"ig", "instagram", "instagram feed", "instagram carousel"}:
        return PLATFORM_INSTAGRAM
    if lowered in {"fb", "facebook", "facebook page", "fb page"}:
        return PLATFORM_FACEBOOK
    return raw


def _brand_label(brand: str) -> str:
    lowered = brand.strip().lower()
    if lowered == "funlab":
        return "FUNLAB"
    if lowered == "powkong":
        return "POWKONG"
    return brand.strip()


def _default_account_name(brand: str, platform: str) -> str:
    if not brand or not platform:
        return ""
    suffix = "IG" if platform == PLATFORM_INSTAGRAM else "FB" if platform == PLATFORM_FACEBOOK else ""
    if not suffix:
        return ""
    return f"{_brand_label(brand)} {suffix}"


def _looks_like_public_handle_or_page(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("@") or " page" in lowered or lowered.endswith(" page")


def _account_name(fields: dict[str, Any], brand: str, platform: str) -> str:
    explicit = text_value(_first_value(fields, ("计划发布账号", "账号名称")))
    if explicit:
        return explicit
    raw_account = text_value(_first_value(fields, ("账号", "账号展示名", "账号handle")))
    default = _default_account_name(brand, platform)
    if raw_account and not _looks_like_public_handle_or_page(raw_account):
        return raw_account
    return default or raw_account


def _default_slots(fields: dict[str, Any], platform: str) -> list[str]:
    slot_value = _first_value(fields, ("发布位置", "发布渠道", "发布版位"))
    if slot_value:
        raw = select_value(slot_value)
        if raw.lower() in {"instagram", "ig", "instagram feed"}:
            return [SLOT_IG_FEED]
        if raw.lower() in {"instagram carousel", "ig carousel", "carousel"}:
            return [SLOT_IG_CAROUSEL]
        if raw.lower() in {"facebook", "facebook page", "fb", "fb page"}:
            return [SLOT_FB_PAGE]
        if isinstance(slot_value, list):
            return [text_value(item) for item in slot_value if text_value(item)]
        return [raw]
    material_type = select_value(fields.get("素材类型")).lower()
    if platform == PLATFORM_INSTAGRAM:
        return [SLOT_IG_CAROUSEL if material_type == "carousel" else SLOT_IG_FEED]
    if platform == PLATFORM_FACEBOOK:
        return [SLOT_FB_PAGE]
    return []


def build_social_crm_p1_fields(record: dict[str, Any]) -> dict[str, Any]:
    fields = dict(record.get("fields", record))
    mapped = dict(fields)

    brand = select_value(_first_value(fields, ("品牌", "brand"))).strip()
    platform = normalize_platform(_first_value(fields, ("平台", "platform", "渠道")))
    account_name = _account_name(fields, brand, platform)

    if brand:
        mapped["品牌"] = brand
    if platform:
        mapped["平台"] = [platform]
    if account_name and not text_value(mapped.get("计划发布账号")):
        mapped["计划发布账号"] = account_name

    slots = _default_slots(fields, platform)
    if slots and not mapped.get("发布位置"):
        mapped["发布位置"] = slots

    caption = text_value(_first_value(fields, ("Caption EN", "发帖文案", "正文", "内容正文", "caption")))
    if caption and not text_value(mapped.get("Caption EN")):
        mapped["Caption EN"] = caption
    hashtags = text_value(_first_value(fields, ("Hashtag EN", "Hashtags", "话题标签", "hashtag")))
    if hashtags and not text_value(mapped.get("Hashtag EN")):
        mapped["Hashtag EN"] = hashtags

    image_url = text_value(_first_value(fields, ("主图URL", "发布图片URL", "图片URL", "媒体URL", "素材链接", "AI生成图链接")))
    if image_url and not parse_urls(mapped.get("主图URL")):
        mapped["主图URL"] = image_url
    file_token = _first_value(fields, ("生成图片file_token", "图片file_token", "素材file_token", "主图file_token"))
    if file_token and not parse_file_tokens(mapped.get("生成图片file_token")):
        mapped["生成图片file_token"] = file_token

    schedule = _first_value(fields, ("计划发布时间", "计划发布时间(北京时间)", "scheduled_at"))
    if schedule and not text_value(mapped.get("计划发布时间")):
        mapped["计划发布时间"] = schedule

    material_type = select_value(_first_value(fields, ("素材类型", "内容类型", "material_type"))).strip()
    if material_type and not text_value(mapped.get("素材类型")):
        mapped["素材类型"] = "carousel" if material_type.lower() in {"carousel", "ig carousel"} else material_type
    elif not text_value(mapped.get("素材类型")):
        mapped["素材类型"] = "single_image"

    approval_result = _first_value(fields, ("审批结果", "人审结果", "审核结果"))
    if "审批通过" not in mapped and approval_result is not None:
        mapped["审批通过"] = _passed(approval_result)

    final_asset_value = _first_value(fields, FINAL_ASSET_FIELDS)
    if "最终素材确认" not in mapped and final_asset_value is not None:
        mapped["最终素材确认"] = _passed(final_asset_value)

    risk = select_value(_first_value(fields, ("审批风险等级", "风险等级", "risk_level")))
    if risk and not text_value(mapped.get("审批风险等级")):
        mapped["审批风险等级"] = risk
    elif not text_value(mapped.get("审批风险等级")):
        mapped["审批风险等级"] = "normal"

    if not text_value(mapped.get("实验变量")):
        mapped["实验变量"] = "Social CRM P1 canary"

    if not text_value(mapped.get("发布模式")):
        mapped["发布模式"] = "auto"

    status = select_value(mapped.get("状态"))
    draft_status = select_value(_first_value(fields, ("草稿状态", "draft_status")))
    if not status:
        mapped["状态"] = "待发布" if draft_status in READY_DRAFT_VALUES and _passed(approval_result) else draft_status

    return mapped


def build_social_crm_p1_publish_request(req: SocialCrmP1PublishRequest, raw_record: dict[str, Any]) -> PublishRequest:
    record_id = req.record_id or raw_record.get("record_id", "inline-social-crm-p1")
    mapped_fields = build_social_crm_p1_fields(raw_record)
    return PublishRequest(
        record_id=record_id,
        record={"record_id": record_id, "fields": mapped_fields},
        account_config=req.account_config,
        recent_records=req.recent_records,
        now=req.now,
    )


def social_crm_p1_precheck(
    req: SocialCrmP1PublishRequest,
    mapped_fields: dict[str, Any],
    *,
    commit: bool,
    p1_publish_enabled: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    platform = select_value(mapped_fields.get("平台"))
    if platform not in ALLOWED_PLATFORMS:
        issues.append(
            {
                "code": "PLATFORM_NOT_P1",
                "message": "Social CRM P1 canary only supports Meta Facebook/Instagram publishing.",
            }
        )
    if commit:
        if not req.canary:
            issues.append({"code": "CANARY_REQUIRED", "message": "Commit requires canary=true for a single controlled post."})
        if req.source != "manual":
            issues.append({"code": "MANUAL_SOURCE_REQUIRED", "message": "Commit requires source=manual; cron/auto commit is blocked."})
        if not p1_publish_enabled:
            issues.append({"code": "P1_PUBLISH_DISABLED", "message": "SOCIAL_CRM_P1_PUBLISH_ENABLED=false"})
        if not text_value(mapped_fields.get("真实发布授权时间")) and not bool_value(mapped_fields.get("真实发布授权")):
            issues.append(
                {
                    "code": "AUTHORIZATION_TIME_MISSING",
                    "message": "Commit requires 真实发布授权时间 or 真实发布授权=true in the request record.",
                }
            )
    return issues


def social_crm_p1_writeback_hint(result: dict[str, Any]) -> dict[str, str]:
    run_id = text_value(result.get("run_id"))
    status = text_value(result.get("status"))
    if result.get("ok"):
        if status == "published":
            link = text_value((result.get("result") or {}).get("前台链接"))
            return {"dry-run 结果": f"pass; run_id={run_id}", "发后回读链接": link}
        return {"dry-run 结果": f"pass; run_id={run_id}"}
    blocking = result.get("blocking") or []
    codes = ",".join([text_value(item.get("code")) for item in blocking if isinstance(item, dict)])
    return {"dry-run 结果": f"blocked; run_id={run_id}; codes={codes}"}
