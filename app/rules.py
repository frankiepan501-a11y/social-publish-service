from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse


STATUS_READY = "待发布"
MODE_AUTO = "auto"
MODE_DRY_RUN = "dry-run"
MODE_MANUAL = "manual"
PLATFORM_INSTAGRAM = "Instagram"
PLATFORM_FACEBOOK = "Facebook"
SLOT_IG_FEED = "IG Feed"
SLOT_IG_CAROUSEL = "IG Carousel"
SLOT_FB_PAGE = "FB Page"
DISALLOWED_SLOTS = {"IG Reels", "IG Stories", "FB Reels", "FB Group"}

GENERAL_BLOCK_TERMS = [
    "official nintendo",
    "nintendo official",
    "nintendo-licensed",
    "pokemon",
    "pokémon",
    "mario",
    "zelda",
    "piranha plant",
    "razer",
    "8bitdo",
    "gamesir",
    "guaranteed cure",
    "best in the world",
]


@dataclass
class ValidationIssue:
    code: str
    message: str
    level: str = "error"


@dataclass
class AccountConfig:
    account_name: str
    brand: str
    platform: str
    publish_slots: list[str] = field(default_factory=list)
    meta_page_id: str = ""
    ig_user_id: str = ""
    daily_limit: int = 1
    weekly_limit: int = 3
    min_interval_hours: int = 24
    enabled: bool = False
    default_mode: str = MODE_DRY_RUN
    timezone_name: str = "America/New_York"
    posting_windows: str = ""

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> "AccountConfig":
        return cls(
            account_name=text_value(fields.get("账号名称")),
            brand=select_value(fields.get("品牌")),
            platform=select_value(fields.get("平台")),
            publish_slots=multi_value(fields.get("发布位置")),
            meta_page_id=text_value(fields.get("Meta Page ID")),
            ig_user_id=text_value(fields.get("Instagram User ID")),
            daily_limit=int(number_value(fields.get("每日上限"), 1)),
            weekly_limit=int(number_value(fields.get("每周上限"), 3)),
            min_interval_hours=int(number_value(fields.get("最小间隔小时"), 24)),
            enabled=bool_value(fields.get("自动发布启用")),
            default_mode=select_value(fields.get("默认发布模式")) or MODE_DRY_RUN,
            timezone_name=text_value(fields.get("账号时区")) or "America/New_York",
            posting_windows=text_value(fields.get("允许发布时间窗")),
        )


@dataclass
class ValidationResult:
    ok: bool
    input_hash: str
    blocking: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    decision_reason: str = ""
    normalized: dict[str, Any] = field(default_factory=dict)


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        if "text" in value:
            return text_value(value["text"])
        if "link" in value:
            return text_value(value["link"])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        parts = [text_value(item) for item in value]
        return ", ".join([part for part in parts if part])
    return str(value).strip()


def select_value(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return ""
        return text_value(value[0])
    return text_value(value)


def multi_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text_value(item) for item in value if text_value(item)]
    raw = text_value(value)
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,;/\n]+", raw) if item.strip()]


def _hashtag_tokens(value: Any) -> list[str]:
    raw = text_value(value)
    if not raw:
        return []
    tokens: list[str] = []
    for part in re.split(r"[\s,;，；]+", raw):
        tag = part.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        if tag not in tokens:
            tokens.append(tag)
    return tokens


def build_publish_caption(fields: dict[str, Any]) -> str:
    caption = text_value(fields.get("Caption EN"))
    hashtags = _hashtag_tokens(fields.get("Hashtag EN"))
    if not hashtags:
        return caption
    caption_lower = caption.lower()
    missing = [tag for tag in hashtags if tag.lower() not in caption_lower]
    if not missing:
        return caption
    suffix = " ".join(missing)
    if not caption:
        return suffix
    return f"{caption}\n\n{suffix}"

def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    raw = text_value(value).lower()
    return raw in {"1", "true", "yes", "y", "on", "checked", "通过", "是"}


def number_value(value: Any, default: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = text_value(value)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_dt(value: Any) -> datetime | None:
    raw = text_value(value)
    if not raw:
        return None
    if raw.isdigit():
        ts = int(raw)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def parse_urls(value: Any) -> list[str]:
    raw = text_value(value)
    if not raw:
        return []
    urls = []
    for part in re.split(r"[\n,]+", raw):
        item = part.strip()
        if item:
            urls.append(item)
    return urls


def parse_file_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        tokens: list[str] = []
        for item in value:
            if isinstance(item, dict):
                token = text_value(item.get("file_token") or item.get("token"))
                if token:
                    tokens.append(token)
            else:
                tokens.extend(parse_file_tokens(text_value(item)))
        return tokens
    if isinstance(value, dict):
        token = text_value(value.get("file_token") or value.get("token"))
        return [token] if token else []
    raw = text_value(value)
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[\n,;]+", raw) if item.strip()]


def is_public_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_publishable_asset_url(url: str) -> bool:
    if not is_public_url(url):
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.endswith("feishu.cn") and (path.startswith("/drive/") or path.startswith("/base/") or path.startswith("/wiki/")):
        return False
    return True


def collect_single_asset_urls(fields: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for field_name in ("主图URL", "public_asset_url", "发布图片URL", "AI生成图链接", "素材链接"):
        for url in parse_urls(fields.get(field_name)):
            if url not in urls:
                urls.append(url)
    return urls


def collect_single_asset_file_tokens(fields: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for field_name in ("生成图片file_token", "图片候选file_token", "素材file_token", "主图file_token"):
        for token in parse_file_tokens(fields.get(field_name)):
            if token not in tokens:
                tokens.append(token)
    return tokens


def input_hash(fields: dict[str, Any], account: AccountConfig | None) -> str:
    payload = {
        "fields": fields,
        "account": account.__dict__ if account else None,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_fields(record: dict[str, Any]) -> dict[str, Any]:
    if "fields" in record and isinstance(record["fields"], dict):
        return record["fields"]
    return record


def validate_publish(
    record: dict[str, Any],
    account: AccountConfig | None,
    recent_records: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    commit: bool = False,
    ig_limit: dict[str, Any] | None = None,
) -> ValidationResult:
    fields = normalize_fields(record)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    blocking: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    normalized: dict[str, Any] = {}

    def error(code: str, message: str) -> None:
        blocking.append(ValidationIssue(code=code, message=message, level="error"))

    def warn(code: str, message: str) -> None:
        warnings.append(ValidationIssue(code=code, message=message, level="warning"))

    if account is None:
        error("ACCOUNT_MISSING", "未找到计划发布账号配置")
    else:
        normalized["account"] = account.account_name
        if not account.enabled:
            if commit:
                error("ACCOUNT_DISABLED", "账号配置未启用自动发布")
            else:
                warn("ACCOUNT_DISABLED", "账号配置当前未启用自动发布；dry-run 只做规则演练")
        if commit and account.default_mode != MODE_AUTO:
            error("ACCOUNT_NOT_AUTO", "账号默认发布模式不是 auto")

    status = select_value(fields.get("状态"))
    if status != STATUS_READY:
        error("STATUS_NOT_READY", f"状态必须是 {STATUS_READY}，当前为 {status or '空'}")

    mode = select_value(fields.get("发布模式")) or (account.default_mode if account else MODE_DRY_RUN)
    normalized["publish_mode"] = mode
    if mode == MODE_MANUAL:
        error("MODE_MANUAL", "发布模式为 manual，自动发布服务不得处理")
    if commit and mode != MODE_AUTO:
        error("MODE_NOT_AUTO", "commit 只处理发布模式=auto 的记录")

    if bool_value(fields.get("发布锁")):
        error("PUBLISH_LOCKED", "发布锁已打开，禁止重复发布")

    if not bool_value(fields.get("审批通过")):
        error("APPROVAL_MISSING", "运营一审未通过")
    if not bool_value(fields.get("最终素材确认")):
        error("ASSET_NOT_CONFIRMED", "最终素材未确认")

    risk = select_value(fields.get("审批风险等级")) or "normal"
    normalized["risk"] = risk
    if risk == "blocked":
        error("RISK_BLOCKED", "审批风险等级为 blocked")
    if risk == "high-risk" and not bool_value(fields.get("二审通过")):
        error("SECOND_REVIEW_MISSING", "高风险内容必须二审通过")

    exp = select_value(fields.get("实验变量"))
    if not exp:
        error("EXPERIMENT_MISSING", "每条内容必须绑定 1 个实验变量")
    normalized["experiment"] = exp

    caption = text_value(fields.get("Caption EN"))
    final_caption = build_publish_caption(fields)
    normalized["caption"] = caption
    normalized["hashtags"] = " ".join(_hashtag_tokens(fields.get("Hashtag EN")))
    normalized["publish_caption"] = final_caption
    if not caption:
        error("CAPTION_MISSING", "Caption EN 不能为空")
    elif len(final_caption) > 2200 and account and account.platform == PLATFORM_INSTAGRAM:
        error("CAPTION_TOO_LONG", "Instagram caption 超过 2200 字符")
    for term in GENERAL_BLOCK_TERMS:
        if term in final_caption.lower():
            error("CAPTION_BLOCK_TERM", f"Caption/Hashtag 命中禁用/高风险词：{term}")

    slots = multi_value(fields.get("发布位置"))
    if not slots:
        platform = select_value(fields.get("平台"))
        material_type = select_value(fields.get("素材类型")) or "single_image"
        if platform == PLATFORM_INSTAGRAM:
            slots = [SLOT_IG_CAROUSEL if material_type == "carousel" else SLOT_IG_FEED]
        elif platform == PLATFORM_FACEBOOK:
            slots = [SLOT_FB_PAGE]
    normalized["slots"] = slots

    bad_slots = sorted(set(slots) & DISALLOWED_SLOTS)
    if bad_slots:
        error("UNSUPPORTED_SLOT", f"v1 不支持发布位置：{', '.join(bad_slots)}")
    if account and account.platform == PLATFORM_INSTAGRAM and not set(slots) & {SLOT_IG_FEED, SLOT_IG_CAROUSEL}:
        error("ACCOUNT_SLOT_MISMATCH", "Instagram 账号只能发布 IG Feed / IG Carousel")
    if account and account.platform == PLATFORM_FACEBOOK and slots != [SLOT_FB_PAGE]:
        error("ACCOUNT_SLOT_MISMATCH", "Facebook 账号 v1 只允许 FB Page 图片帖")

    material_type = select_value(fields.get("素材类型")) or "single_image"
    normalized["material_type"] = material_type
    if material_type == "carousel":
        urls = parse_urls(fields.get("Carousel素材URL"))
        file_tokens = parse_file_tokens(fields.get("Carousel素材file_token"))
        if len(urls) < 2 or len(urls) > 10:
            if len(file_tokens) < 2 or len(file_tokens) > 10:
                error("CAROUSEL_COUNT", "IG Carousel 素材数量必须为 2-10 张")
        for url in urls:
            if not is_publishable_asset_url(url):
                error("ASSET_URL_INVALID", f"素材 URL 不可公网访问：{url}")
        if account and account.platform != PLATFORM_INSTAGRAM:
            error("CAROUSEL_PLATFORM", "Carousel v1 只支持 Instagram")
        normalized["asset_urls"] = urls
        normalized["asset_file_tokens"] = file_tokens
        if file_tokens and not urls:
            warn("ASSET_PUBLIC_URL_PENDING", "Carousel 只有 file_token，commit 前必须先转换成公网素材 URL")
    else:
        urls = collect_single_asset_urls(fields)
        file_tokens = collect_single_asset_file_tokens(fields)
        public_urls = []
        for url in urls:
            if not is_publishable_asset_url(url):
                error("ASSET_URL_INVALID", f"素材 URL 不可公网访问：{url}")
            else:
                public_urls.append(url)
        if not public_urls and not file_tokens:
            error("ASSET_MISSING", "缺少主图URL / 素材链接 / AI生成图链接 / 生成图片file_token")
        if file_tokens and not public_urls:
            warn("ASSET_PUBLIC_URL_PENDING", "只有 file_token，commit 前必须先转换成公网素材 URL")
        normalized["asset_urls"] = public_urls[:1]
        normalized["asset_file_tokens"] = file_tokens[:1]

    scheduled_at = parse_dt(fields.get("计划发布时间"))
    if commit and scheduled_at and scheduled_at > now + timedelta(minutes=1):
        error("SCHEDULE_NOT_DUE", "计划发布时间未到")
    normalized["scheduled_at"] = scheduled_at.isoformat() if scheduled_at else ""

    if account:
        _validate_frequency(fields, account, recent_records or [], now, error)
        if account.platform == PLATFORM_INSTAGRAM and ig_limit:
            quota_total = int(number_value(ig_limit.get("quota_total"), 0))
            quota_usage = int(number_value(ig_limit.get("quota_usage"), 0))
            if quota_total and quota_usage >= quota_total:
                error("IG_API_QUOTA", f"Instagram content publishing limit 已达上限 {quota_usage}/{quota_total}")

    ok = not blocking
    reason = "pass" if ok else "; ".join([issue.code for issue in blocking])
    return ValidationResult(
        ok=ok,
        input_hash=input_hash(fields, account),
        blocking=blocking,
        warnings=warnings,
        decision_reason=reason,
        normalized=normalized,
    )


def _validate_frequency(
    fields: dict[str, Any],
    account: AccountConfig,
    recent_records: list[dict[str, Any]],
    now: datetime,
    error,
) -> None:
    account_name = account.account_name
    brand = account.brand
    day_cutoff = now - timedelta(hours=24)
    week_cutoff = now - timedelta(days=7)
    account_day = 0
    account_week = 0
    brand_day = 0
    offer_week = 0
    latest: datetime | None = None

    for record in recent_records:
        item = normalize_fields(record)
        published_at = parse_dt(item.get("实际发布时间"))
        if not published_at:
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        item_account = text_value(item.get("计划发布账号"))
        item_brand = select_value(item.get("品牌"))
        if item_account == account_name and published_at >= week_cutoff:
            account_week += 1
            if bool_value(item.get("权益内容")):
                offer_week += 1
        if item_account == account_name and published_at >= day_cutoff:
            account_day += 1
        if item_brand == brand and published_at >= day_cutoff:
            brand_day += 1
        if item_account == account_name:
            latest = published_at if latest is None else max(latest, published_at)

    if account_day >= account.daily_limit:
        error("DAILY_LIMIT", f"{account_name} 24h 内已达每日上限 {account.daily_limit}")
    if account_week >= account.weekly_limit:
        error("WEEKLY_LIMIT", f"{account_name} 7 天内已达每周上限 {account.weekly_limit}")
    if brand_day >= 2:
        error("BRAND_DAILY_LIMIT", f"{brand} 品牌 24h 内已达 2 条自动发布上限")
    if bool_value(fields.get("权益内容")) and offer_week >= 1:
        error("OFFER_WEEKLY_LIMIT", f"{account_name} 7 天内已有活动权益内容")
    if latest:
        delta_hours = (now - latest).total_seconds() / 3600
        if delta_hours < account.min_interval_hours:
            error(
                "MIN_INTERVAL",
                f"{account_name} 距离上次发布时间 {delta_hours:.1f}h，小于 {account.min_interval_hours}h",
            )
