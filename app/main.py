from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from .approval import approval_card_preview, approval_update_fields
from .config import Settings, get_settings
from .discovery import (
    DEFAULT_ACCOUNT_SLOTS,
    apply_kol_action,
    apply_reference_action,
    build_kol_feishu_cards,
    build_kol_candidates,
    build_kol_review_cards,
    build_kol_visual_post_candidates,
    build_product_index_fields,
    build_reference_discovery_candidates,
    build_weekly_input_feishu_card,
    build_weekly_input_card,
    discovery_input_hash,
    lock_weekly_strategies,
    normalize_account_records,
)
from .feishu_client import FeishuClient, FeishuError
from .generation import (
    DEFAULT_REFERENCE_STRATEGY,
    build_update_fields,
    design_reference_images,
    detail_reference_images,
    generate_payload,
    generation_candidate_reason,
    generation_input_hash,
    funlab_ip_compliance_issue,
    has_product_reference_image,
    image_generation_requires_reference,
    product_asset_directory,
    product_reference_images,
    required_generation_missing,
    validate_generation_payload,
)
from .meta_client import MetaApiError, MetaClient
from .models import ApprovalActionRequest, ApprovalCardPreviewRequest
from .models import GenerateBriefRequest, GenerateScanRequest, InsightsPollRequest, PublishRequest, PublishScanRequest, ReplayRequest
from .models import PlanDailyConfirmRequest, PlanReselectRequest, PlanWeeklyRequest
from .models import SocialCrmP0SyncRequest, SocialCrmP1PublishRequest
from .models import (
    KolActionRequest,
    KolVisualPostDiscoveryRequest,
    KolWeeklyDiscoveryRequest,
    ProductIndexSyncRequest,
    ReferenceActionRequest,
    ReferenceWeeklyDiscoveryRequest,
    WeeklyInputActionRequest,
    WeeklyInputCardRequest,
)
from .models import ImageResultIngestRequest, ImageTaskRequest, ImageTaskScanRequest
from .models import ImageResultIngestScanRequest
from .planning import (
    build_daily_confirm_card,
    build_daily_confirm_feishu_card,
    build_weekly_candidates,
    default_strategies_from_accounts,
    apply_plan_action,
    select_daily_candidates,
)
from .rules import (
    AccountConfig,
    MODE_AUTO,
    PLATFORM_FACEBOOK,
    PLATFORM_INSTAGRAM,
    collect_single_asset_file_tokens,
    parse_file_tokens,
    validate_publish,
    bool_value,
    normalize_fields,
    parse_dt,
    select_value,
    text_value,
)
from .social_crm_p0 import run_social_crm_p0_sync
from .social_crm_p1 import (
    build_social_crm_p1_publish_request,
    social_crm_p1_precheck,
    social_crm_p1_writeback_hint,
)


app = FastAPI(title="social-publish-service", version="0.1.0")


def _check_auth(settings: Settings, authorization: str | None) -> None:
    if not settings.service_token:
        return
    expected = f"Bearer {settings.service_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _platform_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"none", "null", "nan"}:
        return ""
    return text

def _feishu_writeback_detail(exc: FeishuError) -> dict[str, str]:
    return {
        "code": "FEISHU_WRITEBACK_FAILED",
        "message": str(exc),
    }


WEEKLY_POOL_UPDATE_FIELDS = {
    "候选标题",
    "内容支柱",
    "实验变量",
    "SEO主关键词",
    "GEO目标问题",
    "搜索意图",
    "语义实体词",
    "长尾关键词",
    "Hashtag词组池",
    "SEO/GEO生成说明",
    "目标落地页",
    "参考对象",
    "参考对象链接",
    "参考理由",
    "借鉴元素",
    "禁止复制元素",
    "重推次数",
    "日确认状态",
    "运行/回放ID",
    "内容日历record_id",
}


def _weekly_pool_update_fields(updates: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in updates.items() if key in WEEKLY_POOL_UPDATE_FIELDS}


def _parse_now(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_issues(result):
    return {
        "blocking": [issue.__dict__ for issue in result.blocking],
        "warnings": [issue.__dict__ for issue in result.warnings],
        "decision_reason": result.decision_reason,
    }


def _feishu(settings: Settings) -> FeishuClient | None:
    if not settings.feishu_enabled():
        return None
    app_id = settings.feishu_bitable_app_id or settings.feishu_app_id
    app_secret = settings.feishu_bitable_app_secret or settings.feishu_app_secret
    return FeishuClient(app_id, app_secret, settings.feishu_base_token)


def _feishu_image(settings: Settings) -> FeishuClient | None:
    if not settings.image_task_enabled():
        return None
    return FeishuClient(settings.feishu_app_id, settings.feishu_app_secret, settings.image_task_base_token)


def _feishu_product(settings: Settings) -> FeishuClient | None:
    if not settings.product_library_enabled():
        return None
    return FeishuClient(settings.feishu_app_id, settings.feishu_app_secret, settings.product_library_base_token)


def _product_table_id_for_brand(settings: Settings, brand: str) -> str:
    return settings.product_funlab_table_id if brand.upper() == "FUNLAB" else settings.product_powkong_table_id


def _norm_match(value: str) -> str:
    return text_value(value).lower().replace(" ", "").replace("-", "")


def _ref_name(ref: dict) -> str:
    return text_value(ref.get("name")) or text_value(ref.get("file_name")) or text_value(ref.get("url"))


def _pick_refs(refs: list[dict], *, max_count: int, prefer: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> list[dict]:
    if not refs or max_count <= 0:
        return []

    def score(ref: dict) -> tuple[int, int]:
        name = _ref_name(ref).lower()
        preferred = sum(1 for marker in prefer if marker.lower() in name)
        avoided = sum(1 for marker in avoid if marker.lower() in name)
        return preferred - avoided, -refs.index(ref)

    ranked = sorted(refs, key=score, reverse=True)
    selected: list[dict] = []
    seen_tokens: set[str] = set()
    for ref in ranked:
        token = text_value(ref.get("file_token")) or _ref_name(ref)
        if token in seen_tokens:
            continue
        selected.append(ref)
        seen_tokens.add(token)
        if len(selected) >= max_count:
            break
    return selected


def _select_image_task_references(
    product_refs: list[dict],
    design_refs: list[dict],
    detail_refs: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    selected_design = _pick_refs(
        design_refs,
        max_count=1,
        prefer=("设计", "竞品", "社媒", "scene", "design", "reference"),
    )
    selected_detail = _pick_refs(
        detail_refs,
        max_count=1,
        prefer=("细节", "特写", "按键", "图标", "中间", "detail", "button", "close"),
    )
    selected_product = _pick_refs(
        product_refs,
        max_count=1,
        prefer=("正面主图", "主图", "正面", "front", "main", "image-02", "图2", "02"),
        avoid=("细节", "特写", "按键", "图标", "接口", "顶部", "背面", "45", "斜", "detail", "button", "port", "top", "back"),
    )
    return selected_product, selected_design, selected_detail


def _merge_product_context_fields(fields: dict, product_record: dict | None) -> dict:
    if not product_record:
        return fields
    product_fields = normalize_fields(product_record)
    merged = dict(fields)
    product_record_id = text_value(product_record.get("record_id"))
    if product_record_id and not text_value(merged.get("产品库记录ID")):
        merged["产品库记录ID"] = product_record_id

    image_value = product_fields.get("图片")
    if image_value and not has_product_reference_image(merged):
        merged["产品参考图包"] = image_value
        merged["产品参考图"] = image_value
        merged["产品库图片"] = image_value

    field_map = {
        "产品库产品简述": "产品简述",
        "产品库系列英文名": "系列英文名",
        "产品库型号英文名": "型号英文名",
        "产品库适配IP/IP联想": "适配IP/IP联想",
        "产品库IP合规状态": "IP合规状态",
        "产品库IP合规备注": "IP合规备注",
    }
    for target, source in field_map.items():
        value = product_fields.get(source)
        if value and not text_value(merged.get(target)):
            merged[target] = value
    if not text_value(merged.get("IP合规状态")) and product_fields.get("IP合规状态"):
        merged["IP合规状态"] = product_fields.get("IP合规状态")
    if not text_value(merged.get("IP合规备注")) and product_fields.get("IP合规备注"):
        merged["IP合规备注"] = product_fields.get("IP合规备注")
    if not text_value(merged.get("适配IP/IP联想")) and product_fields.get("适配IP/IP联想"):
        merged["适配IP/IP联想"] = product_fields.get("适配IP/IP联想")
    if not text_value(merged.get("产品名")):
        merged["产品名"] = (
            text_value(product_fields.get("产品中文名"))
            or text_value(product_fields.get("型号中文名"))
            or text_value(product_fields.get("型号英文名"))
        )
    if not text_value(merged.get("品牌型号/SKU")):
        merged["品牌型号/SKU"] = (
            text_value(product_fields.get("品牌型号V2"))
            or text_value(product_fields.get("品牌型号"))
            or text_value(product_fields.get("ERP SKU"))
        )
    if not text_value(merged.get("主推卖点")) and product_fields.get("产品简述"):
        merged["主推卖点"] = product_fields.get("产品简述")
    return merged


async def _load_product_record_for_content(fields: dict, settings: Settings) -> dict | None:
    brand = select_value(fields.get("品牌")) or "Powkong"
    table_id = _product_table_id_for_brand(settings, brand)
    client = _feishu_product(settings)
    if client is None:
        return None

    record_id = text_value(fields.get("产品库记录ID"))
    if record_id:
        return await client.get_record(table_id, record_id)

    sku = text_value(fields.get("品牌型号/SKU"))
    if not sku:
        return None
    needle = _norm_match(sku)
    for record in await client.list_records(table_id, page_size=200):
        product_fields = normalize_fields(record)
        candidates = [
            text_value(product_fields.get("品牌型号V2")),
            text_value(product_fields.get("品牌型号")),
            text_value(product_fields.get("ERP SKU")),
            text_value(product_fields.get("报价单型号")),
            text_value(product_fields.get("工厂型号")),
            text_value(product_fields.get("型号中文名")),
            text_value(product_fields.get("型号英文名")),
        ]
        if any(_norm_match(candidate) == needle for candidate in candidates if candidate):
            return record
    return None


async def _enrich_content_fields_with_product_context(fields: dict, settings: Settings) -> dict:
    if text_value(fields.get("_product_context_loaded")):
        return fields
    enriched = dict(fields)
    try:
        product_record = await _load_product_record_for_content(enriched, settings)
    except FeishuError as exc:
        enriched["_product_context_error"] = str(exc)
        return enriched
    enriched = _merge_product_context_fields(enriched, product_record)
    enriched["_product_context_loaded"] = "true" if product_record else "false"
    return enriched


async def _load_record(req: PublishRequest, settings: Settings) -> tuple[str, dict]:
    if req.record:
        return req.record_id or req.record.get("record_id", "inline"), req.record
    if not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.record_id, await client.get_record(settings.content_table_id, req.record_id)


async def _load_generate_record(req: GenerateBriefRequest, settings: Settings) -> tuple[str, dict]:
    if req.record:
        return req.record_id or req.record.get("record_id", "inline"), req.record
    if not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.record_id, await client.get_record(settings.content_table_id, req.record_id)


async def _load_image_task_source(req: ImageTaskRequest, settings: Settings) -> tuple[str, dict]:
    if req.record:
        return req.record_id or req.record.get("record_id", "inline"), req.record
    if not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.record_id, await client.get_record(settings.content_table_id, req.record_id)


async def _load_image_result_source(req: ImageResultIngestRequest, settings: Settings) -> tuple[str, dict]:
    if req.record:
        return req.record_id or req.record.get("record_id", "inline"), req.record
    if not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.record_id, await client.get_record(settings.content_table_id, req.record_id)


async def _load_approval_record(req: ApprovalActionRequest | ApprovalCardPreviewRequest, settings: Settings) -> tuple[str, dict]:
    if req.record:
        return req.record_id or req.record.get("record_id", "inline"), req.record
    if not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.record_id, await client.get_record(settings.content_table_id, req.record_id)


async def _load_image_task_record(req: ImageResultIngestRequest, settings: Settings, content_fields: dict) -> tuple[str, dict]:
    if req.image_task_record:
        return req.image_task_record_id or req.image_task_record.get("record_id", "inline-image-task"), req.image_task_record
    task_record_id = req.image_task_record_id or text_value(content_fields.get("图片任务record_id"))
    if not task_record_id:
        raise HTTPException(status_code=400, detail="image_task_record_id or image_task_record is required")
    client = _feishu_image(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu image task env is not configured")
    return task_record_id, await client.get_record(settings.image_task_table_id, task_record_id)


async def _load_account(req: PublishRequest, record: dict, settings: Settings) -> AccountConfig | None:
    if req.account_config:
        return AccountConfig.from_fields(req.account_config.get("fields", req.account_config))
    fields = record.get("fields", record)
    account_name = str(fields.get("计划发布账号", "")).strip()
    if not account_name:
        return None
    client = _feishu(settings)
    if client is None:
        return None
    account_record = await client.find_account(settings.account_table_id, account_name)
    if not account_record:
        return None
    return AccountConfig.from_fields(account_record.get("fields", {}))


async def _load_recent_records(req: PublishRequest, settings: Settings) -> list[dict]:
    if req.recent_records:
        return req.recent_records
    client = _feishu(settings)
    if client is None:
        return []
    return await client.list_records(settings.content_table_id, page_size=200)


async def _write_log(settings: Settings, **kwargs) -> None:
    if kwargs.get("mode") == "dry-run" and not settings.dry_run_write_logs:
        return
    client = _feishu(settings)
    if client is None:
        return
    try:
        await client.append_run_log(settings.log_table_id, **kwargs)
    except FeishuError:
        # Logging failure must not hide the dry-run/commit decision.
        return


async def _load_plan_records(req: PlanWeeklyRequest, settings: Settings) -> tuple[list[dict], list[dict], list[dict]]:
    if req.strategies or req.references or req.reviews:
        return req.strategies, req.references, req.reviews
    client = _feishu(settings)
    if client is None:
        return [], [], []

    async def list_or_empty(table_id: str) -> list[dict]:
        if not table_id:
            return []
        try:
            return await client.list_records(table_id, page_size=200)
        except Exception:
            return []

    strategies = await list_or_empty(settings.strategy_table_id)
    references = await list_or_empty(settings.reference_table_id)
    reviews = await list_or_empty(settings.weekly_review_table_id)
    if not strategies:
        accounts = await list_or_empty(settings.account_table_id)
        if not accounts:
            accounts = [{"fields": fields} for fields in DEFAULT_ACCOUNT_SLOTS]
        strategies = [{"fields": fields} for fields in default_strategies_from_accounts(accounts)]
    return strategies, references, reviews


async def _load_plan_candidates(req: PlanDailyConfirmRequest, settings: Settings) -> list[dict]:
    if req.candidates:
        return req.candidates
    client = _feishu(settings)
    if client is None:
        return []
    try:
        return await client.list_records(settings.weekly_pool_table_id, page_size=200)
    except Exception:
        return []


async def _load_plan_candidate(req: PlanReselectRequest, settings: Settings) -> tuple[str, dict]:
    if req.candidate:
        return req.candidate_record_id or req.candidate.get("record_id", "inline-weekly-candidate"), req.candidate
    if not req.candidate_record_id:
        raise HTTPException(status_code=400, detail="candidate_record_id or candidate is required")
    client = _feishu(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    return req.candidate_record_id, await client.get_record(settings.weekly_pool_table_id, req.candidate_record_id)


async def _load_reference_records(req_references: list[dict[str, Any]], settings: Settings) -> list[dict]:
    if req_references:
        return req_references
    client = _feishu(settings)
    if client is None:
        return []
    try:
        return await client.list_records(settings.reference_table_id, page_size=200)
    except Exception:
        return []


async def _load_account_records(req_accounts: list[dict[str, Any]], settings: Settings) -> list[dict]:
    if req_accounts:
        return req_accounts
    client = _feishu(settings)
    if client is None:
        return []
    try:
        return await client.list_records(settings.account_table_id, page_size=200)
    except Exception:
        return [{"fields": item} for item in DEFAULT_ACCOUNT_SLOTS]


async def _load_product_index_records(req_product_index: list[dict[str, Any]], settings: Settings) -> list[dict]:
    if req_product_index:
        return req_product_index
    client = _feishu(settings)
    if client is not None and settings.product_index_table_id:
        try:
            records = await client.list_records(settings.product_index_table_id, page_size=200)
            if records:
                return records
        except Exception:
            pass
    product_client = _feishu_product(settings)
    if product_client is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        funlab = await product_client.list_records(settings.product_funlab_table_id, page_size=200)
        rows.extend([{"fields": item} for item in build_product_index_fields(funlab, brand_hint="FUNLAB")])
    except Exception:
        pass
    try:
        powkong = await product_client.list_records(settings.product_powkong_table_id, page_size=200)
        rows.extend([{"fields": item} for item in build_product_index_fields(powkong, brand_hint="Powkong")])
    except Exception:
        pass
    return rows


async def _load_strategy_records(req_strategies: list[dict[str, Any]], settings: Settings) -> list[dict]:
    if req_strategies:
        return req_strategies
    client = _feishu(settings)
    if client is None:
        return []
    try:
        strategies = await client.list_records(settings.strategy_table_id, page_size=200)
    except Exception:
        strategies = []
    if strategies:
        return strategies
    try:
        accounts = await client.list_records(settings.account_table_id, page_size=200)
    except Exception:
        accounts = [{"fields": item} for item in DEFAULT_ACCOUNT_SLOTS]
    return [{"fields": fields} for fields in default_strategies_from_accounts(accounts)]


async def _load_kol_candidates(req_candidates: list[dict[str, Any]], settings: Settings) -> list[dict]:
    if req_candidates:
        return req_candidates
    client = _feishu(settings)
    if client is None or not settings.kol_candidate_table_id:
        return []
    try:
        return await client.list_records(settings.kol_candidate_table_id, page_size=200)
    except Exception:
        return []


async def _load_kol_candidate(req: KolActionRequest, settings: Settings) -> tuple[str, dict]:
    if req.candidate:
        return req.candidate_record_id or req.candidate.get("record_id", "inline-kol-candidate"), req.candidate
    if not req.candidate_record_id:
        raise HTTPException(status_code=400, detail="candidate_record_id or candidate is required")
    client = _feishu(settings)
    if client is None or not settings.kol_candidate_table_id:
        raise HTTPException(status_code=503, detail="Feishu KOL candidate table is not configured")
    return req.candidate_record_id, await client.get_record(settings.kol_candidate_table_id, req.candidate_record_id)


async def _prepare_kol_image_keys(candidates: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    if not candidates:
        return []
    client = _feishu(settings)
    if client is None:
        return [{"reason": "FEISHU_NOT_CONFIGURED", "candidate": text_value(item.get("账号名称"))} for item in candidates]
    errors: list[dict[str, Any]] = []
    for candidate in candidates:
        name = text_value(candidate.get("账号名称")) or "kol-reference"
        for index in (1, 2):
            image_field = f"样例帖子{index}图片"
            key_field = f"样例帖子{index}图片Key"
            if text_value(candidate.get(key_field)):
                continue
            image_url = text_value(candidate.get(image_field))
            if not image_url:
                continue
            try:
                candidate[key_field] = await client.upload_message_image_from_url(
                    image_url,
                    file_name=f"fbig_kol_{name}_{index}.png",
                )
            except Exception as exc:
                error_field = f"样例帖子{index}图片Key错误"
                candidate[error_field] = f"{type(exc).__name__}: {str(exc)[:300]}"
                errors.append(
                    {
                        "candidate": name,
                        "image_field": image_field,
                        "image_url": image_url,
                        "reason": candidate[error_field],
                    }
                )
    return errors


KOL_CANDIDATE_CARD_ONLY_FIELDS = {
    "样例帖子1图片Key",
    "样例帖子2图片Key",
    "样例帖子1图片Key错误",
    "样例帖子2图片Key错误",
}


def _kol_candidate_persist_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key not in KOL_CANDIDATE_CARD_ONLY_FIELDS}


def _kol_review_record_with_card_fields(created_item: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    record = created_item.get("record") if isinstance(created_item, dict) else None
    if not isinstance(record, dict):
        return None
    record_fields = record.get("fields")
    if not isinstance(record_fields, dict):
        return record
    merged_fields = dict(candidate)
    merged_fields.update(record_fields)
    record_id = text_value(record.get("record_id") or record.get("id"))
    if record_id:
        merged_fields["record_id"] = record_id
    for field_name in KOL_CANDIDATE_CARD_ONLY_FIELDS:
        if candidate.get(field_name):
            merged_fields[field_name] = candidate[field_name]
    return {**record, "fields": merged_fields}


def _plan_gate_blocked(settings: Settings, run_id: str, node: str, record_id: str) -> dict | None:
    if settings.plan_writeback_enabled:
        return None
    return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "PLAN_WRITEBACK_DISABLED", "node": node, "record_id": record_id}]}


async def _execute_weekly_input_card(req: WeeklyInputCardRequest, settings: Settings) -> dict:
    run_id = "weeklyinputcardv1-" + uuid.uuid4().hex[:16]
    accounts = await _load_account_records(req.accounts, settings)
    product_index = await _load_product_index_records(req.product_index, settings)
    card = build_weekly_input_card(accounts, product_index, week_start=req.week_start, now=req.now)
    feishu_card = build_weekly_input_feishu_card(card)
    await _write_log(
        settings,
        run_id=run_id,
        record_id="weekly-input-card",
        node="plan_weekly_input_card",
        status="success",
        input_hash=discovery_input_hash({"week_start": req.week_start, "accounts": len(accounts), "products": len(product_index)}),
        output_summary=f"accounts={len(card['accounts'])}, products={len(product_index)}",
        decision_reason="weekly_input_card_built",
        mode="dry-run",
    )
    return {"ok": True, "status": "weekly-input-card", "run_id": run_id, "card": card, "feishu_card": feishu_card}


async def _execute_weekly_input_action(req: WeeklyInputActionRequest, settings: Settings) -> dict:
    run_id = "weeklyinputv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "plan_weekly_input_action", "strategy")
        if blocked:
            await _write_log(
                settings,
                run_id=run_id,
                record_id="strategy",
                node="plan_weekly_input_action",
                status="blocked",
                input_hash="",
                output_summary="weekly input writeback disabled",
                decision_reason="PLAN_WRITEBACK_DISABLED",
                mode="dry-run",
            )
            return blocked
    accounts = await _load_account_records(req.accounts, settings)
    product_index = await _load_product_index_records(req.product_index, settings)
    strategies = lock_weekly_strategies(accounts, req.submissions, product_index, week_start=req.week_start, now=req.now)
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for strategy in strategies:
            created.append(await client.create_record(settings.strategy_table_id, strategy))
    await _write_log(
        settings,
        run_id=run_id,
        record_id="strategy",
        node="plan_weekly_input_action",
        status="success",
        input_hash=discovery_input_hash({"week_start": req.week_start, "submissions": req.submissions}),
        output_summary=f"strategies={len(strategies)}, created={len(created)}",
        decision_reason="weekly_strategy_locked",
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "weekly-input-written" if req.write_back else "weekly-input-dry-run",
        "run_id": run_id,
        "strategies": strategies,
        "created": created,
    }


async def _execute_product_index_sync(req: ProductIndexSyncRequest, settings: Settings) -> dict:
    run_id = "productindexv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "plan_product_index_sync", "product-index")
        if blocked:
            return blocked
        if not settings.product_index_table_id:
            raise HTTPException(status_code=503, detail="Feishu product index table is not configured")
    product_records = req.product_records
    if not product_records:
        product_client = _feishu_product(settings)
        if product_client is None:
            product_records = []
        elif req.brand.upper() == "FUNLAB":
            product_records = await product_client.list_records(settings.product_funlab_table_id, page_size=200)
        elif req.brand.upper() == "POWKONG":
            product_records = await product_client.list_records(settings.product_powkong_table_id, page_size=200)
        else:
            funlab = await product_client.list_records(settings.product_funlab_table_id, page_size=200)
            powkong = await product_client.list_records(settings.product_powkong_table_id, page_size=200)
            rows = build_product_index_fields(funlab, brand_hint="FUNLAB") + build_product_index_fields(powkong, brand_hint="Powkong")
            product_records = [{"fields": item} for item in rows]
    rows = build_product_index_fields(product_records, brand_hint="" if req.brand == "all" else req.brand)[: req.limit]
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for row in rows:
            created.append(await client.create_record(settings.product_index_table_id, row))
    await _write_log(
        settings,
        run_id=run_id,
        record_id="product-index",
        node="plan_product_index_sync",
        status="success",
        input_hash=discovery_input_hash({"brand": req.brand, "records": len(product_records)}),
        output_summary=f"rows={len(rows)}, created={len(created)}",
        decision_reason="product_index_built",
        mode="commit" if req.write_back else "dry-run",
    )
    return {"ok": True, "status": "product-index-written" if req.write_back else "product-index-dry-run", "run_id": run_id, "rows": rows, "created": created}


async def _execute_reference_weekly(req: ReferenceWeeklyDiscoveryRequest, settings: Settings) -> dict:
    run_id = "refdiscoverv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "discovery_reference_weekly", "reference")
        if blocked:
            return blocked
    strategies = await _load_strategy_records(req.strategies, settings)
    references = await _load_reference_records(req.references, settings)
    candidates = build_reference_discovery_candidates(
        strategies,
        references,
        week_start=req.week_start,
        now=req.now,
        limit_per_brand=req.limit_per_brand,
    )
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for candidate in candidates:
            created.append(await client.create_record(settings.reference_table_id, candidate))
    await _write_log(
        settings,
        run_id=run_id,
        record_id="reference",
        node="discovery_reference_weekly",
        status="success",
        input_hash=discovery_input_hash({"week_start": req.week_start, "strategies": len(strategies), "references": len(references)}),
        output_summary=f"candidates={len(candidates)}, created={len(created)}",
        decision_reason="reference_discovery_candidates_built",
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "reference-discovery-written" if req.write_back else "reference-discovery-dry-run",
        "run_id": run_id,
        "candidates": candidates,
        "created": created,
        "notes": ["candidates default to 待确认; only 状态=可用 references are eligible for weekly planning"],
    }


async def _execute_kol_weekly(req: KolWeeklyDiscoveryRequest, settings: Settings) -> dict:
    run_id = "koldiscoverv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "discovery_kol_weekly", "kol")
        if blocked:
            return blocked
        if not settings.kol_candidate_table_id:
            raise HTTPException(status_code=503, detail="Feishu KOL candidate table is not configured")
    strategies = await _load_strategy_records(req.strategies, settings)
    existing = await _load_kol_candidates(req.existing_candidates, settings)
    rejected: list[dict[str, Any]] = []
    if req.visual_posts:
        candidates, rejected = build_kol_visual_post_candidates(
            strategies,
            req.visual_posts,
            existing,
            week_start=req.week_start,
            now=req.now,
            per_brand=req.per_brand,
            min_score=req.min_visual_score,
        )
        decision_reason = "kol_visual_post_candidates_built"
    else:
        candidates = build_kol_candidates(strategies, existing, week_start=req.week_start, now=req.now, per_brand=req.per_brand)
        decision_reason = "kol_candidates_built"
    image_key_errors = []
    if req.prepare_image_keys:
        image_key_errors = await _prepare_kol_image_keys(candidates, settings)
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for candidate in candidates:
            try:
                created.append(await client.create_record(settings.kol_candidate_table_id, _kol_candidate_persist_fields(candidate)))
            except FeishuError as exc:
                raise HTTPException(status_code=502, detail=f"Feishu KOL candidate write failed: {exc}") from exc
    review_records: list[dict[str, Any]] = []
    if created:
        for item, candidate in zip(created, candidates):
            record = _kol_review_record_with_card_fields(item, candidate)
            if record is not None:
                review_records.append(record)
    if not review_records:
        review_records = [{"fields": item} for item in candidates]
    cards = build_kol_review_cards(review_records, week_start=req.week_start)
    feishu_cards = build_kol_feishu_cards(cards)
    await _write_log(
        settings,
        run_id=run_id,
        record_id="kol",
        node="discovery_kol_weekly",
        status="success",
        input_hash=discovery_input_hash(
            {
                "week_start": req.week_start,
                "strategies": len(strategies),
                "existing": len(existing),
                "visual_posts": len(req.visual_posts),
                "min_visual_score": req.min_visual_score,
                "prepare_image_keys": req.prepare_image_keys,
            }
        ),
        output_summary=f"candidates={len(candidates)}, rejected={len(rejected)}, cards={len(cards)}, created={len(created)}, image_key_errors={len(image_key_errors)}",
        decision_reason=decision_reason,
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "kol-discovery-written" if req.write_back else "kol-discovery-dry-run",
        "run_id": run_id,
        "candidates": candidates,
        "rejected": rejected,
        "cards": cards,
        "feishu_cards": feishu_cards,
        "created": created,
        "image_key_errors": image_key_errors,
        "notes": [
            "KOL candidates are gated by IG/FB image-post sample links; account homepages, YouTube links, Reels/video pages, and generic websites are excluded.",
            "When visual_posts are provided, this weekly endpoint uses the same visual-post scoring path as /discovery/kol/visual-posts so n8n or a discovery agent can repush cards through the existing weekly KOL branch.",
            "If candidates=0, do not send a KOL review card; run visual-reference discovery or ask operations to add verified IG/FB image post URLs.",
        ],
    }


async def _execute_kol_visual_posts(req: KolVisualPostDiscoveryRequest, settings: Settings) -> dict:
    run_id = "kolvisualpostv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "discovery_kol_visual_posts", "kol")
        if blocked:
            return blocked
        if not settings.kol_candidate_table_id:
            raise HTTPException(status_code=503, detail="Feishu KOL candidate table is not configured")
    strategies = await _load_strategy_records(req.strategies, settings)
    existing = await _load_kol_candidates(req.existing_candidates, settings)
    candidates, rejected = build_kol_visual_post_candidates(
        strategies,
        req.posts,
        existing,
        week_start=req.week_start,
        now=req.now,
        per_brand=req.per_brand,
        min_score=req.min_score,
    )
    image_key_errors = []
    if req.prepare_image_keys:
        image_key_errors = await _prepare_kol_image_keys(candidates, settings)
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for candidate in candidates:
            try:
                created.append(await client.create_record(settings.kol_candidate_table_id, _kol_candidate_persist_fields(candidate)))
            except FeishuError as exc:
                raise HTTPException(status_code=502, detail=f"Feishu KOL candidate write failed: {exc}") from exc
    review_records: list[dict[str, Any]] = []
    if created:
        for item, candidate in zip(created, candidates):
            record = _kol_review_record_with_card_fields(item, candidate)
            if record is not None:
                review_records.append(record)
    if not review_records:
        review_records = [{"fields": item} for item in candidates]
    cards = build_kol_review_cards(review_records, week_start=req.week_start)
    feishu_cards = build_kol_feishu_cards(cards)
    await _write_log(
        settings,
        run_id=run_id,
        record_id="kol-visual-posts",
        node="discovery_kol_visual_posts",
        status="success",
        input_hash=discovery_input_hash(
            {
                "week_start": req.week_start,
                "strategies": len(strategies),
                "posts": len(req.posts),
                "existing": len(existing),
                "min_score": req.min_score,
                "prepare_image_keys": req.prepare_image_keys,
            }
        ),
        output_summary=f"candidates={len(candidates)}, rejected={len(rejected)}, cards={len(cards)}, created={len(created)}, image_key_errors={len(image_key_errors)}",
        decision_reason="kol_visual_post_candidates_built",
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "kol-visual-posts-written" if req.write_back else "kol-visual-posts-dry-run",
        "run_id": run_id,
        "candidates": candidates,
        "rejected": rejected,
        "cards": cards,
        "feishu_cards": feishu_cards,
        "created": created,
        "image_key_errors": image_key_errors,
        "notes": [
            "This endpoint scores already-collected public IG/FB image-post candidates. It does not bypass login walls or scrape private content.",
            "Ready candidates must include a post-level IG/FB image-post URL and a thumbnail/screenshot URL before operations review.",
        ],
    }


async def _execute_kol_action(req: KolActionRequest, settings: Settings) -> dict:
    run_id = "kolactionv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "discovery_kol_action", req.candidate_record_id or "kol")
        if blocked:
            return blocked
        if not settings.kol_candidate_table_id:
            raise HTTPException(status_code=503, detail="Feishu KOL candidate table is not configured")
    candidate_id, candidate = await _load_kol_candidate(req, settings)
    strategies = await _load_strategy_records(req.strategies, settings)
    existing = await _load_kol_candidates(req.existing_candidates, settings)
    action_result = apply_kol_action(
        candidate,
        action=req.action,
        strategies=strategies,
        existing_candidates=existing,
        replacement_count=req.replacement_count,
        week_start=req.week_start,
        now=req.now,
    )
    candidate_update = None
    reference_record = None
    replacement_records: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        if candidate_id != "inline-kol-candidate":
            candidate_update = await client.update_record(settings.kol_candidate_table_id, candidate_id, action_result["updates"])
        if action_result.get("reference_fields"):
            reference_record = await client.create_record(settings.reference_table_id, action_result["reference_fields"])
        for replacement in action_result.get("replacements", []):
            replacement_records.append(await client.create_record(settings.kol_candidate_table_id, replacement))
    await _write_log(
        settings,
        run_id=run_id,
        record_id=candidate_id,
        node="discovery_kol_action",
        status="success",
        input_hash=discovery_input_hash({"candidate_id": candidate_id, "action": req.action}),
        output_summary=f"action={req.action}, replacements={len(action_result.get('replacements', []))}, reference={bool(action_result.get('reference_fields'))}",
        decision_reason=req.action,
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "kol-action-written" if req.write_back else "kol-action-dry-run",
        "run_id": run_id,
        "candidate_record_id": candidate_id,
        "fields": action_result["updates"],
        "reference_fields": action_result.get("reference_fields"),
        "replacements": action_result.get("replacements", []),
        "candidate_update": candidate_update,
        "reference_record": reference_record,
        "replacement_records": replacement_records,
    }


async def _execute_reference_action(req: ReferenceActionRequest, settings: Settings) -> dict:
    run_id = "refactionv1-" + uuid.uuid4().hex[:16]
    if req.write_back:
        blocked = _plan_gate_blocked(settings, run_id, "discovery_reference_action", req.candidate_record_id or "reference")
        if blocked:
            return blocked
        if not settings.kol_candidate_table_id:
            raise HTTPException(status_code=503, detail="Feishu reference candidate table is not configured")
    candidate_id, candidate = await _load_kol_candidate(req, settings)
    strategies = await _load_strategy_records(req.strategies, settings)
    existing = await _load_kol_candidates(req.existing_candidates, settings)
    action_result = apply_reference_action(
        candidate,
        action=req.action,
        strategies=strategies,
        visual_posts=req.visual_posts,
        existing_candidates=existing,
        replacement_count=req.replacement_count,
        min_score=req.min_score,
        week_start=req.week_start,
        now=req.now,
    )
    candidate_update = None
    reference_record = None
    replacement_records: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        if candidate_id != "inline-kol-candidate":
            candidate_update = await client.update_record(settings.kol_candidate_table_id, candidate_id, action_result["updates"])
        if action_result.get("reference_fields"):
            reference_record = await client.create_record(settings.reference_table_id, action_result["reference_fields"])
        for replacement in action_result.get("replacements", []):
            replacement_records.append(await client.create_record(settings.kol_candidate_table_id, replacement))
    await _write_log(
        settings,
        run_id=run_id,
        record_id=candidate_id,
        node="discovery_reference_action",
        status="success",
        input_hash=discovery_input_hash({"candidate_id": candidate_id, "action": req.action}),
        output_summary=f"action={req.action}, replacements={len(action_result.get('replacements', []))}, reference={bool(action_result.get('reference_fields'))}",
        decision_reason=req.action,
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "reference-action-written" if req.write_back else "reference-action-dry-run",
        "run_id": run_id,
        "candidate_record_id": candidate_id,
        "fields": action_result["updates"],
        "reference_fields": action_result.get("reference_fields"),
        "replacements": action_result.get("replacements", []),
        "candidate_update": candidate_update,
        "reference_record": reference_record,
        "replacement_records": replacement_records,
    }


async def _execute_plan_weekly(req: PlanWeeklyRequest, settings: Settings) -> dict:
    run_id = "planweeklyv1-" + uuid.uuid4().hex[:16]
    if req.write_back and not settings.plan_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id="weekly-pool",
            node="plan_weekly",
            status="blocked",
            input_hash="",
            output_summary="weekly plan writeback disabled",
            decision_reason="PLAN_WRITEBACK_DISABLED",
            mode="dry-run",
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "PLAN_WRITEBACK_DISABLED"}]}

    strategies, references, reviews = await _load_plan_records(req, settings)
    candidates = build_weekly_candidates(
        strategies,
        references,
        week_start=req.week_start,
        now=req.now,
        limit=req.limit,
    )
    created: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for fields in candidates:
            to_write = dict(fields)
            to_write["运行/回放ID"] = run_id
            created.append(await client.create_record(settings.weekly_pool_table_id, to_write))

    await _write_log(
        settings,
        run_id=run_id,
        record_id="weekly-pool",
        node="plan_weekly",
        status="success",
        input_hash=hashlib.sha256(json.dumps({"week_start": req.week_start, "count": len(strategies)}, ensure_ascii=False).encode()).hexdigest(),
        output_summary=f"generated={len(candidates)}, created={len(created)}",
        decision_reason="weekly_plan_generated",
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "weekly-plan-written" if req.write_back else "weekly-plan-dry-run",
        "run_id": run_id,
        "generated": len(candidates),
        "created": created,
        "candidates": candidates,
        "notes": [
            "weekly pool only; records are not publishable until daily confirmation creates content calendar rows",
            "SOCIAL_PLAN_WRITEBACK_ENABLED gates all Base writes for plan endpoints",
        ],
    }


async def _execute_plan_daily_confirm(req: PlanDailyConfirmRequest, settings: Settings) -> dict:
    run_id = "plandailyv1-" + uuid.uuid4().hex[:16]
    if req.write_back and not settings.plan_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id="daily-confirm",
            node="plan_daily_confirm",
            status="blocked",
            input_hash="",
            output_summary="daily confirm writeback disabled",
            decision_reason="PLAN_WRITEBACK_DISABLED",
            mode="dry-run",
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "PLAN_WRITEBACK_DISABLED"}]}

    candidates = await _load_plan_candidates(req, settings)
    selected = select_daily_candidates(candidates, target_date=req.target_date or req.now, limit=req.limit)
    cards = [build_daily_confirm_card(candidate) for candidate in selected]
    feishu_cards = [build_daily_confirm_feishu_card(card, run_id=run_id) for card in cards]
    updated: list[dict] = []
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        for candidate in selected:
            record_id = candidate.get("record_id") or candidate.get("id")
            if record_id:
                updated.append(
                    await client.update_record(
                        settings.weekly_pool_table_id,
                        record_id,
                        {
                            "日确认状态": "已推送",
                            "运行/回放ID": run_id,
                        },
                    )
                )

    await _write_log(
        settings,
        run_id=run_id,
        record_id="daily-confirm",
        node="plan_daily_confirm",
        status="success",
        input_hash=hashlib.sha256(json.dumps({"target_date": req.target_date or req.now}, ensure_ascii=False).encode()).hexdigest(),
        output_summary=f"selected={len(selected)}, cards={len(cards)}, updated={len(updated)}",
        decision_reason="daily_confirm_cards_built",
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "daily-confirm-written" if req.write_back else "daily-confirm-dry-run",
        "run_id": run_id,
        "selected": len(selected),
        "cards": cards,
        "feishu_cards": feishu_cards,
        "updated": updated,
    }


async def _execute_plan_reselect(req: PlanReselectRequest, settings: Settings) -> dict:
    run_id = "planreselectv1-" + uuid.uuid4().hex[:16]
    if req.write_back and not settings.plan_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=req.candidate_record_id or "inline-weekly-candidate",
            node="plan_reselect",
            status="blocked",
            input_hash="",
            output_summary="plan action writeback disabled",
            decision_reason="PLAN_WRITEBACK_DISABLED",
            mode="dry-run",
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "PLAN_WRITEBACK_DISABLED"}]}

    candidate_id, candidate = await _load_plan_candidate(req, settings)
    references = await _load_reference_records(req.references, settings) if req.action == "reselect_reference" else req.references
    action_result = apply_plan_action(
        candidate,
        action=req.action,
        references=references,
        reschedule_date=req.reschedule_date,
    )
    updates = dict(action_result["updates"])
    updates["运行/回放ID"] = run_id
    content_fields = action_result.get("content_fields")
    content_record: dict | None = None
    weekly_update: dict | None = None
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        try:
            if req.action == "confirm_generate" and content_fields:
                content_record = await client.create_record(settings.content_table_id, content_fields)
                new_record_id = (
                    content_record.get("record", {}).get("record_id")
                    or content_record.get("record_id")
                    or content_record.get("id")
                    or ""
                )
                updates["内容日历record_id"] = new_record_id
                updates["日确认状态"] = "已生成内容日历"
            if candidate_id != "inline-weekly-candidate":
                weekly_update = await client.update_record(
                    settings.weekly_pool_table_id,
                    candidate_id,
                    _weekly_pool_update_fields(updates),
                )
        except FeishuError as exc:
            detail = _feishu_writeback_detail(exc)
            await _write_log(
                settings,
                run_id=run_id,
                record_id=candidate_id,
                node="plan_reselect",
                status="failed",
                input_hash=hashlib.sha256(
                    json.dumps({"candidate_id": candidate_id, "action": req.action}, ensure_ascii=False).encode()
                ).hexdigest(),
                output_summary=detail["message"][:500],
                decision_reason=detail["code"],
                mode="commit",
            )
            raise HTTPException(status_code=502, detail=detail) from exc

    await _write_log(
        settings,
        run_id=run_id,
        record_id=candidate_id,
        node="plan_reselect",
        status="success",
        input_hash=hashlib.sha256(json.dumps({"candidate_id": candidate_id, "action": req.action}, ensure_ascii=False).encode()).hexdigest(),
        output_summary=f"action={req.action}, content_created={bool(content_record)}",
        decision_reason=req.action,
        mode="commit" if req.write_back else "dry-run",
    )
    return {
        "ok": True,
        "status": "plan-action-written" if req.write_back else "plan-action-dry-run",
        "run_id": run_id,
        "candidate_record_id": candidate_id,
        "fields": updates,
        "content_fields": content_fields,
        "content_record": content_record,
        "weekly_update": weekly_update,
    }


def _image_ratio_for_content(fields: dict) -> str:
    material_type = select_value(fields.get("素材类型"))
    slots = fields.get("发布位置")
    slot_text = " ".join(slots if isinstance(slots, list) else [text_value(slots)])
    if "Stories" in slot_text or "Reels" in slot_text:
        return "9:16"
    if material_type == "carousel":
        return "1:1"
    return "1:1"


def _build_image_task_fields(record_id: str, fields: dict) -> tuple[dict, list[str], str]:
    product = text_value(fields.get("产品名")) or text_value(fields.get("内容标题"))
    brand = select_value(fields.get("品牌"))
    status = select_value(fields.get("状态"))
    task_brand = "Funlab" if brand.upper() == "FUNLAB" else brand
    image_prompt = text_value(fields.get("AI图片Prompt"))
    mode = select_value(fields.get("图片生成模式")) or "Codex Image"
    scene_template = select_value(fields.get("场景模板")) or "FB/INS广告图"
    size = text_value(fields.get("图片生成尺寸")) or _image_ratio_for_content(fields)
    raw_references = product_reference_images(fields)
    raw_design_refs = design_reference_images(fields)
    raw_detail_refs = detail_reference_images(fields)
    references, design_refs, detail_refs = _select_image_task_references(
        raw_references,
        raw_design_refs,
        raw_detail_refs,
    )
    reference_strategy = select_value(fields.get("参考图使用策略")) or DEFAULT_REFERENCE_STRATEGY
    asset_directory = product_asset_directory(fields)
    missing = []
    if mode != "Codex Image":
        missing.append("图片生成模式 must be Codex Image")
    if not product:
        missing.append("产品名 or 内容标题")
    if not image_prompt:
        missing.append("AI图片Prompt")
    if image_generation_requires_reference(fields) and not references:
        missing.append("产品参考图/产品原图")
    ip_issue = funlab_ip_compliance_issue(fields)
    if ip_issue:
        missing.append(ip_issue)
    if text_value(fields.get("图片任务record_id")) and not bool(fields.get("_force_image_task")):
        missing.append("图片任务record_id already exists")
    if (
        bool_value(fields.get("审批通过"))
        or bool_value(fields.get("最终素材确认"))
        or status in {"待发布", "发布中", "已发布"}
    ) and not bool_value(fields.get("_approval_regeneration_image_task")):
        missing.append("approved/publish-ready content requires approval regeneration action")

    custom_prompt = "\n".join(
        [
            f"Source content_record_id: {record_id}",
            "Use case: FB/IG organic content candidate image.",
            "Generate a review candidate only.",
            f"Reference strategy: {reference_strategy}. If reference inputs conflict, product fidelity wins over scene style.",
            (
                f"Product asset directory mapping: {asset_directory}. "
                "When this value is present, it is the stable product-asset source path that should supply "
                "产品正面主图/产品细节图 attachments before falling back to generic product-library images."
                if asset_directory
                else "Product asset directory mapping: not provided; use only the attached product references."
            ),
            (
                "Reference roles: 设计参考图 is for scene, composition, mood, lighting, camera framing, and borrowing distance only; "
                "do not copy competitor logos, text, product designs, platform/game UI, or brand marks from it."
            ),
            (
                "Reference roles: 产品参考图包 is the product source of truth for shape, proportions, color, material, buttons, "
                "ports, textures, visible markings, and accessory layout."
            ),
            (
                "Reference roles: 细节参考图 overrides the broader product pack for small controls, icons, ports, surface patterns, "
                "and other detail fidelity."
            ),
            (
                "Reference budget: only selected master/detail references are attached to this task to avoid multi-angle fusion. "
                "The full source pack may contain more images in Base, but do not blend unselected product angles or detail shots."
            ),
            (
                "If 细节参考图 is provided, preserve the shown small-control count, relative positions, button shapes, icon shapes, "
                "and icon directions. Do not reinterpret round/dot/home/minus/plus icons as letters, squares, generic symbols, "
                "or decorative marks."
            ),
            (
                "Mandatory product rule: use the attached 产品参考图包/产品原图/reference images as the exact source of truth. "
                "Do not redesign, recolor, morph, simplify, replace, or invent product parts. "
                "Only change the surrounding scene, lighting, camera angle, background, and composition."
            ),
            "Do not add visible text, new logos, watermarks, copyrighted characters, or competitor products.",
            image_prompt,
        ]
    )
    feedback_patch = text_value(fields.get("图片重生Patch"))
    feedback_change = text_value(fields.get("本轮只改什么"))
    feedback_keep = text_value(fields.get("本轮必须保留"))
    if feedback_patch or feedback_change or feedback_keep:
        custom_prompt = "\n".join(
            [
                custom_prompt,
                "Regeneration instructions from the approval card:",
                "Keep these approved elements:",
                feedback_keep or "(none specified)",
                "Only change these elements:",
                feedback_change or "(none specified)",
                "Structured feedback patch JSON:",
                feedback_patch or "{}",
            ]
        )
    task_fields = {
        "产品名称": product,
        "品牌": task_brand,
        "工作流选择": "Codex Image",
        "状态": "待处理",
        "尺寸选择": size,
        "场景模板": scene_template,
        "参考图使用策略": reference_strategy,
        "自定义提示词": custom_prompt,
    }
    if references:
        task_fields["产品参考图包"] = references
        task_fields["产品原图"] = references
    if design_refs:
        task_fields["设计参考图"] = design_refs
    if detail_refs:
        task_fields["细节参考图"] = detail_refs
    raw = json.dumps(task_fields, ensure_ascii=False, sort_keys=True, default=str)
    return task_fields, missing, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reference_file_name(ref: dict, index: int, content_type: str) -> str:
    name = text_value(ref.get("name") or ref.get("file_name"))
    if name:
        return name
    ext_by_type = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return f"product_reference_{index + 1}{ext_by_type.get(content_type, '.png')}"


async def _copy_product_references_to_image_base(
    task_fields: dict,
    *,
    content_client: FeishuClient,
    image_client: FeishuClient,
) -> dict:
    attachment_fields = ("产品原图", "产品参考图包", "设计参考图", "细节参考图")
    if not any(isinstance(task_fields.get(field_name), list) and task_fields.get(field_name) for field_name in attachment_fields):
        return task_fields
    next_fields = dict(task_fields)
    copied_by_source_token: dict[str, dict] = {}
    for field_name in attachment_fields:
        refs = task_fields.get(field_name)
        if not isinstance(refs, list) or not refs:
            continue
        copied_refs = []
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            file_token = text_value(ref.get("file_token") or ref.get("token"))
            if not file_token:
                continue
            if file_token not in copied_by_source_token:
                download_url = text_value(ref.get("url"))
                if download_url:
                    content, content_type = await content_client.download_media_url(download_url)
                else:
                    content, content_type = await content_client.download_media(file_token)
                file_name = _reference_file_name(ref, index, content_type)
                copied_token = await image_client.upload_bitable_media(
                    file_name=file_name,
                    content=content,
                    content_type=content_type,
                )
                copied_by_source_token[file_token] = {"file_token": copied_token, "name": file_name}
            copied_refs.append(copied_by_source_token[file_token])
        if copied_refs:
            next_fields[field_name] = copied_refs
    return next_fields


def _is_carousel_content(fields: dict) -> bool:
    material_type = select_value(fields.get("素材类型"))
    raw_slots = fields.get("发布位置")
    slots = raw_slots if isinstance(raw_slots, list) else [text_value(raw_slots)]
    return material_type == "carousel" or any(text_value(slot) == "IG Carousel" for slot in slots)


def _carousel_regeneration_slide_fields(fields: dict, slide_index: int) -> dict:
    slide_label = f"{slide_index}/2"
    if slide_index == 1:
        slide_instruction = (
            "Carousel regeneration slide 1/2: create the main hero scene for the approved carousel. "
            "Use the product front/main reference as the product source of truth. Keep the product standalone, "
            "physically plausible, and unchanged. Do not insert a Switch or other device into the product unless "
            "the product reference explicitly shows that usage."
        )
    else:
        slide_instruction = (
            "Carousel regeneration slide 2/2: create a complementary detail or alternate angle for the same carousel. "
            "Preserve the exact product structure. If an interface or cable location is not visible in the selected "
            "product reference, show no cable, no connector, and no invented port on that face."
        )
    next_fields = dict(fields)
    base_prompt = text_value(next_fields.get("AI图片Prompt"))
    next_fields["AI图片Prompt"] = "\n\n".join(part for part in [base_prompt, slide_instruction] if part)
    next_fields["_carousel_slide_label"] = slide_label
    return next_fields


async def _execute_carousel_image_tasks(
    record_id: str,
    fields: dict,
    *,
    write_task: bool,
    settings: Settings,
) -> dict:
    run_id = f"imgtaskcarouselv1-{uuid.uuid4().hex[:16]}"
    base_fields = await _enrich_content_fields_with_product_context(fields, settings)
    base_fields["_force_image_task"] = True
    base_fields["_approval_regeneration_image_task"] = True
    mode = "commit" if write_task else "dry-run"
    image_tasks = []
    missing = []

    for slide_index in (1, 2):
        slide_fields = _carousel_regeneration_slide_fields(base_fields, slide_index)
        task_fields, task_missing, _task_hash = _build_image_task_fields(record_id, slide_fields)
        task_fields["自定义提示词"] = "\n\n".join(
            [
                task_fields["自定义提示词"],
                f"Carousel output contract: generate exactly slide {slide_index}/2 for this carousel. "
                "This callback must create two separate image tasks; never collapse Carousel regeneration into one single-image task.",
            ]
        )
        raw_task_fields = json.dumps(task_fields, ensure_ascii=False, sort_keys=True, default=str)
        task_hash = hashlib.sha256(raw_task_fields.encode("utf-8")).hexdigest()
        image_tasks.append(
            {
                "slide_index": slide_index,
                "slide_label": slide_fields["_carousel_slide_label"],
                "task_fields": task_fields,
                "input_hash": task_hash,
            }
        )
        if task_missing:
            missing.append({"slide_index": slide_index, "missing": task_missing})

    combined_hash = hashlib.sha256(
        json.dumps(
            [{"slide_index": item["slide_index"], "input_hash": item["input_hash"]} for item in image_tasks],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if missing:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/create-carousel",
            status="error",
            input_hash=combined_hash,
            output_summary=json.dumps(missing, ensure_ascii=False),
            decision_reason="CAROUSEL_IMAGE_TASK_INPUT_MISSING",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "missing": missing, "image_tasks": image_tasks}

    if write_task and not settings.image_task_write_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/create-carousel-gate",
            status="blocked",
            input_hash=combined_hash,
            output_summary="SOCIAL_IMAGE_TASK_WRITE_ENABLED=false",
            decision_reason="IMAGE_TASK_WRITE_DISABLED",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "IMAGE_TASK_WRITE_DISABLED"}], "image_tasks": image_tasks}

    if not write_task:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/create-carousel",
            status="dry-run",
            input_hash=combined_hash,
            output_summary=f"carousel_image_tasks={len(image_tasks)}",
            decision_reason="carousel-image-task-dry-run",
            mode=mode,
        )
        return {
            "ok": True,
            "status": "dry-run-carousel-tasks-built",
            "run_id": run_id,
            "task_count": len(image_tasks),
            "image_tasks": image_tasks,
            "input_hash": combined_hash,
        }

    image_client = _feishu_image(settings)
    content_client = _feishu(settings)
    if image_client is None or content_client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")

    created_tasks = []
    for item in image_tasks:
        copied_fields = await _copy_product_references_to_image_base(
            item["task_fields"],
            content_client=content_client,
            image_client=image_client,
        )
        raw_task_fields = json.dumps(copied_fields, ensure_ascii=False, sort_keys=True, default=str)
        item_hash = hashlib.sha256(raw_task_fields.encode("utf-8")).hexdigest()
        created = await image_client.create_record(settings.image_task_table_id, copied_fields)
        image_task_record_id = text_value(created.get("record", {}).get("record_id")) or text_value(created.get("record_id"))
        created_tasks.append(
            {
                "slide_index": item["slide_index"],
                "slide_label": item["slide_label"],
                "image_task_record_id": image_task_record_id,
                "task_fields": copied_fields,
                "input_hash": item_hash,
            }
        )

    task_ids = [item["image_task_record_id"] for item in created_tasks if item.get("image_task_record_id")]
    if record_id != "inline" and task_ids:
        await content_client.update_record(
            settings.content_table_id,
            record_id,
            {
                "图片生成状态": "已提交",
                "图片任务record_id": "\n".join(task_ids),
                "Carousel素材file_token": "",
                "Carousel素材URL": "",
                "public_asset_url": "",
                "主图URL": "",
                "AI生成图链接": "",
                "FB Staged Photo ID": "",
                "运行/回放ID": run_id,
            },
        )

    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="image-task/create-carousel",
        status="success",
        input_hash=combined_hash,
        output_summary=f"carousel_image_task_record_ids={','.join(task_ids)}",
        decision_reason="carousel-image-tasks-created",
        mode=mode,
    )
    return {
        "ok": True,
        "status": "carousel-tasks-created",
        "run_id": run_id,
        "task_count": len(created_tasks),
        "image_task_record_ids": task_ids,
        "image_tasks": created_tasks,
        "input_hash": combined_hash,
    }


def _image_task_candidate_reason(fields: dict, *, force: bool = False) -> tuple[bool, str]:
    status = select_value(fields.get("状态"))
    if status not in {"待审核", "选题中"} and not force:
        return False, f"status={status or 'empty'}"
    mode = select_value(fields.get("图片生成模式")) or "Codex Image"
    if mode != "Codex Image":
        return False, f"image_mode={mode or 'empty'}"
    if not text_value(fields.get("AI图片Prompt")):
        return False, "AI图片Prompt missing"
    if image_generation_requires_reference(fields) and not has_product_reference_image(fields):
        return False, "产品参考图/产品原图 missing"
    ip_issue = funlab_ip_compliance_issue(fields)
    if ip_issue:
        return False, ip_issue
    task_status = select_value(fields.get("图片生成状态"))
    if task_status in {"已提交", "生成中", "已生成-待转URL", "已转发布URL", "失败"} and not force:
        return False, f"image_status={task_status}"
    if text_value(fields.get("图片任务record_id")) and not force:
        return False, "image_task_record_id exists"
    return True, "candidate"


def _image_result_ingest_candidate_reason(fields: dict, *, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "candidate-forced"
    task_record_id = text_value(fields.get("图片任务record_id"))
    if not task_record_id:
        return False, "image_task_record_id missing"
    status = select_value(fields.get("图片生成状态"))
    if status == "已转发布URL":
        return False, "image_result_already_public"
    if status == "失败":
        return False, "image_result_failed"
    if text_value(fields.get("AI生成图链接")) and text_value(fields.get("生成图片file_token")):
        return False, "image_result_already_ingested"
    return True, "candidate"


PENDING_IMAGE_TASK_STATUSES = {"empty", "待处理", "已提交", "生成中", "处理中", "pending", "running"}
FAILED_IMAGE_TASK_STATUSES = {"失败", "处理失败", "failed", "error"}
SUCCESS_IMAGE_TASK_STATUSES = {"处理成功", "已完成", "success"}


def _is_http_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


def _normalize_image_task_status(status: str) -> str:
    if status in PENDING_IMAGE_TASK_STATUSES or status in FAILED_IMAGE_TASK_STATUSES or status in SUCCESS_IMAGE_TASK_STATUSES:
        return status
    try:
        repaired = status.encode("cp949").decode("gbk")
    except UnicodeError:
        return status
    if repaired in PENDING_IMAGE_TASK_STATUSES or repaired in FAILED_IMAGE_TASK_STATUSES or repaired in SUCCESS_IMAGE_TASK_STATUSES:
        return repaired
    return status


def _is_pending_image_result(item: dict) -> bool:
    if item.get("ok"):
        return False
    blocking = item.get("blocking") or []
    codes = [text_value(entry.get("code") if isinstance(entry, dict) else entry) for entry in blocking]
    if not codes:
        return False
    has_missing_result = any(code == "IMAGE_RESULT_MISSING" for code in codes)
    task_not_done_codes = [code for code in codes if code.startswith("IMAGE_TASK_NOT_DONE:")]
    other_codes = [
        code
        for code in codes
        if code != "IMAGE_RESULT_MISSING" and not code.startswith("IMAGE_TASK_NOT_DONE:")
    ]
    if other_codes or not task_not_done_codes:
        return False
    statuses = [code.split(":", 1)[1] or "empty" for code in task_not_done_codes]
    return has_missing_result and all(status in PENDING_IMAGE_TASK_STATUSES for status in statuses)


def _image_task_source_file_tokens(image_task_fields: dict) -> set[str]:
    tokens: set[str] = set()
    for field_name in ("产品原图", "产品参考图包", "设计参考图", "细节参考图"):
        tokens.update(parse_file_tokens(image_task_fields.get(field_name)))
    return {token for token in tokens if token}


def _extract_image_result_fields(image_task_record_id: str, image_task_fields: dict) -> tuple[dict, list[str]]:
    status = _normalize_image_task_status(select_value(image_task_fields.get("状态")))
    file_token = text_value(image_task_fields.get("生成图片file_token"))
    public_url = text_value(image_task_fields.get("public_asset_url")) or text_value(image_task_fields.get("生成图片URL"))
    location = text_value(image_task_fields.get("生成图片位置"))
    error_message = text_value(image_task_fields.get("错误信息"))
    if status in FAILED_IMAGE_TASK_STATUSES:
        updates = {
            "图片任务record_id": image_task_record_id,
            "图片生成状态": "失败",
            "图片生成错误": error_message or f"image task status={status}",
        }
        return updates, []

    blocking = []
    if status not in SUCCESS_IMAGE_TASK_STATUSES:
        blocking.append(f"IMAGE_TASK_NOT_DONE:{status or 'empty'}")
    if not (file_token or public_url or location):
        blocking.append("IMAGE_RESULT_MISSING")
    if file_token and file_token in _image_task_source_file_tokens(image_task_fields):
        blocking.append("IMAGE_RESULT_EQUALS_SOURCE_REFERENCE")

    image_status = "已生成"
    if file_token and not public_url:
        image_status = "已生成-待转URL"
    if public_url:
        image_status = "已转发布URL"
    image_link = public_url or (location if _is_http_url(location) else "")
    updates = {
        "图片任务record_id": image_task_record_id,
        "图片生成状态": image_status,
        "AI生成图链接": image_link,
        "生成图片file_token": file_token,
        "图片生成错误": "",
    }
    if error_message:
        updates["图片生成错误"] = error_message
    return {key: value for key, value in updates.items() if value != ""}, blocking


async def _execute_image_task(req: ImageTaskRequest, settings: Settings) -> dict:
    run_id = f"imgtaskv1-{uuid.uuid4().hex[:16]}"
    record_id, record = await _load_image_task_source(req, settings)
    fields = normalize_fields(record)
    fields = await _enrich_content_fields_with_product_context(fields, settings)
    fields["_force_image_task"] = req.force
    task_fields, missing, task_hash = _build_image_task_fields(record_id, fields)
    mode = "commit" if req.write_task else "dry-run"

    if missing:
        reason = "IMAGE_TASK_INPUT_MISSING"
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/create",
            status="error",
            input_hash=task_hash,
            output_summary="; ".join(missing),
            decision_reason=reason,
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "missing": missing, "task_fields": task_fields}

    if req.write_task and not settings.image_task_write_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/write-gate",
            status="blocked",
            input_hash=task_hash,
            output_summary="SOCIAL_IMAGE_TASK_WRITE_ENABLED=false",
            decision_reason="IMAGE_TASK_WRITE_DISABLED",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "IMAGE_TASK_WRITE_DISABLED"}]}

    if not req.write_task:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/create",
            status="dry-run",
            input_hash=task_hash,
            output_summary=json.dumps(task_fields, ensure_ascii=False),
            decision_reason="image-task-dry-run",
            mode=mode,
        )
        return {"ok": True, "status": "dry-run-task-built", "run_id": run_id, "task_fields": task_fields, "input_hash": task_hash}

    image_client = _feishu_image(settings)
    content_client = _feishu(settings)
    if image_client is None or content_client is None:
        raise HTTPException(status_code=503, detail="Feishu env is not configured")
    task_fields = await _copy_product_references_to_image_base(
        task_fields,
        content_client=content_client,
        image_client=image_client,
    )
    raw_task_fields = json.dumps(task_fields, ensure_ascii=False, sort_keys=True, default=str)
    task_hash = hashlib.sha256(raw_task_fields.encode("utf-8")).hexdigest()
    created = await image_client.create_record(settings.image_task_table_id, task_fields)
    image_task_record_id = text_value(created.get("record", {}).get("record_id")) or text_value(created.get("record_id"))
    if record_id != "inline" and image_task_record_id:
        await content_client.update_record(
            settings.content_table_id,
            record_id,
            {
                "图片生成状态": "已提交",
                "图片任务record_id": image_task_record_id,
                "运行/回放ID": run_id,
            },
        )
    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="image-task/create",
        status="success",
        input_hash=task_hash,
        output_summary=f"image_task_record_id={image_task_record_id}",
        decision_reason="image-task-created",
        mode=mode,
    )
    return {
        "ok": True,
        "status": "task-created",
        "run_id": run_id,
        "image_task_record_id": image_task_record_id,
        "task_fields": task_fields,
    }


async def _execute_image_task_scan(req: ImageTaskScanRequest, settings: Settings) -> dict:
    scan_run_id = f"imgscanv1-{uuid.uuid4().hex[:16]}"
    records = req.records
    if not records:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        records = await client.list_records(settings.content_table_id, page_size=200)

    selected = []
    skipped = []
    for index, record in enumerate(records):
        fields = normalize_fields(record)
        fields = await _enrich_content_fields_with_product_context(fields, settings)
        ok, reason = _image_task_candidate_reason(fields, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, {"record_id": record_id, "fields": fields}))
        else:
            skipped.append({"record_id": record_id, "reason": reason})
        if len(selected) >= req.limit:
            break

    results = []
    for record_id, record in selected:
        item = await _execute_image_task(
            ImageTaskRequest(
                record_id=record_id,
                record=record,
                write_task=req.write_task,
                source=req.source,
                force=req.force,
            ),
            settings=settings,
        )
        item["record_id"] = record_id
        results.append(item)

    created = sum(1 for item in results if item.get("ok"))
    failed = sum(1 for item in results if not item.get("ok"))
    summary = {
        "scanned": len(records),
        "selected": len(selected),
        "created": created,
        "failed": failed,
        "write_task": req.write_task,
        "source": req.source,
        "force": req.force,
        "limit": req.limit,
        "skipped_sample": skipped[:5],
    }
    scan_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    await _write_log(
        settings,
        run_id=scan_run_id,
        record_id="__image_scan__",
        node="image-task/scan",
        status="success" if failed == 0 else "error",
        input_hash=scan_hash,
        output_summary=json.dumps(summary, ensure_ascii=False, default=str),
        decision_reason="image-scan-complete",
        mode="commit" if req.write_task else "dry-run",
        replay_command=(
            'POST /image-task/scan '
            f'{{"write_task":false,"source":"replay","force":{str(req.force).lower()},"limit":{req.limit}}}'
        ),
    )
    return {
        "ok": all(item.get("ok") for item in results) if results else True,
        "status": "image-scan-complete",
        "scan_run_id": scan_run_id,
        "scanned": len(records),
        "selected": len(selected),
        "created": created,
        "failed": failed,
        "write_task": req.write_task,
        "results": results,
        "skipped_sample": skipped[:20],
    }


async def _execute_image_result_ingest_scan(req: ImageResultIngestScanRequest, settings: Settings) -> dict:
    scan_run_id = f"imgingestscanv1-{uuid.uuid4().hex[:16]}"
    records = req.records
    if not records:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        records = await client.list_records(settings.content_table_id, page_size=200)

    selected = []
    skipped = []
    for index, record in enumerate(records):
        fields = normalize_fields(record)
        ok, reason = _image_result_ingest_candidate_reason(fields, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, record))
        else:
            skipped.append({"record_id": record_id, "reason": reason})
        if len(selected) >= req.limit:
            break

    results = []
    for record_id, record in selected:
        item = await _execute_image_result_ingest(
            ImageResultIngestRequest(
                record_id=record_id,
                record=record,
                image_task_record_id=record.get("image_task_record_id"),
                image_task_record=record.get("image_task_record"),
                write_back=req.write_back,
                source=req.source,
            ),
            settings=settings,
        )
        item["record_id"] = record_id
        results.append(item)

    ingested = sum(1 for item in results if item.get("ok"))
    pending = sum(1 for item in results if _is_pending_image_result(item))
    failed = sum(1 for item in results if not item.get("ok") and not _is_pending_image_result(item))
    summary = {
        "scanned": len(records),
        "selected": len(selected),
        "ingested": ingested,
        "pending": pending,
        "failed": failed,
        "write_back": req.write_back,
        "source": req.source,
        "force": req.force,
        "limit": req.limit,
        "skipped_sample": skipped[:5],
    }
    scan_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    await _write_log(
        settings,
        run_id=scan_run_id,
        record_id="__image_ingest_scan__",
        node="image-task/ingest-scan",
        status="success" if failed == 0 else "error",
        input_hash=scan_hash,
        output_summary=json.dumps(summary, ensure_ascii=False, default=str),
        decision_reason="image-ingest-scan-complete",
        mode="commit" if req.write_back else "dry-run",
        replay_command=(
            'POST /image-task/ingest-scan '
            f'{{"write_back":false,"source":"replay","force":{str(req.force).lower()},"limit":{req.limit}}}'
        ),
    )
    return {
        "ok": failed == 0,
        "status": "image-ingest-scan-complete",
        "scan_run_id": scan_run_id,
        "scanned": len(records),
        "selected": len(selected),
        "ingested": ingested,
        "pending": pending,
        "failed": failed,
        "write_back": req.write_back,
        "results": results,
        "skipped_sample": skipped[:20],
    }


async def _execute_image_result_ingest(req: ImageResultIngestRequest, settings: Settings) -> dict:
    run_id = f"imgresultv1-{uuid.uuid4().hex[:16]}"
    record_id, record = await _load_image_result_source(req, settings)
    fields = normalize_fields(record)
    image_task_record_id, image_task_record = await _load_image_task_record(req, settings, fields)
    image_task_fields = normalize_fields(image_task_record)
    updates, blocking = _extract_image_result_fields(image_task_record_id, image_task_fields)
    result_hash = hashlib.sha256(json.dumps(updates, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    mode = "commit" if req.write_back else "dry-run"
    output_summary = json.dumps({"updates": updates, "blocking": blocking}, ensure_ascii=False)

    if blocking:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/ingest",
            status="error",
            input_hash=result_hash,
            output_summary=output_summary,
            decision_reason="; ".join(blocking),
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": item} for item in blocking]}
    if req.write_back and not settings.image_result_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="image-task/ingest",
            status="error",
            input_hash=result_hash,
            output_summary="SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED=false",
            decision_reason="IMAGE_RESULT_WRITEBACK_DISABLED",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "IMAGE_RESULT_WRITEBACK_DISABLED"}]}
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        if record_id == "inline":
            raise HTTPException(status_code=400, detail="write_back requires a persisted record_id")
        try:
            await client.update_record(settings.content_table_id, record_id, updates | {"运行/回放ID": run_id})
        except FeishuError as exc:
            if "1254045" in str(exc) and "运行/回放ID" in str(exc):
                await client.update_record(settings.content_table_id, record_id, updates)
            else:
                await _write_log(
                    settings,
                    run_id=run_id,
                    record_id=record_id,
                    node="image-task/ingest",
                    status="error",
                    input_hash=result_hash,
                    output_summary=output_summary,
                    decision_reason=f"image-result-writeback-failed:{type(exc).__name__}",
                    mode=mode,
                )
                raise
        except Exception as exc:
            await _write_log(
                settings,
                run_id=run_id,
                record_id=record_id,
                node="image-task/ingest",
                status="error",
                input_hash=result_hash,
                output_summary=output_summary,
                decision_reason=f"image-result-writeback-failed:{type(exc).__name__}",
                mode=mode,
            )
            raise
    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="image-task/ingest",
        status="success" if req.write_back else "dry-run",
        input_hash=result_hash,
        output_summary=output_summary,
        decision_reason="image-result-ready",
        mode=mode,
    )
    return {
        "ok": True,
        "status": "image-result-ready" if not req.write_back else "image-result-written",
        "run_id": run_id,
        "fields": updates,
    }


async def _prepare_asset_urls_for_commit(
    *,
    settings: Settings,
    account: AccountConfig,
    record_id: str,
    normalized: dict,
    run_id: str,
) -> tuple[list[str], dict | None]:
    asset_urls = list(normalized.get("asset_urls") or [])
    if asset_urls:
        return asset_urls, None
    file_tokens = list(normalized.get("asset_file_tokens") or [])
    if not file_tokens:
        return asset_urls, None
    if not settings.asset_prepare_enabled:
        return [], {"code": "ASSET_PREPARE_DISABLED", "message": "SOCIAL_ASSET_PREPARE_ENABLED=false"}
    meta_access_token = settings.meta_token_for_brand(account.brand)
    if not meta_access_token:
        return [], {"code": "META_TOKEN_MISSING", "message": "META_ACCESS_TOKEN is required to prepare asset URLs"}
    if not account.meta_page_id:
        return [], {"code": "META_PAGE_ID_MISSING", "message": "Meta Page ID is required to stage images"}
    feishu = _feishu(settings)
    if feishu is None:
        return [], {"code": "FEISHU_NOT_CONFIGURED", "message": "FEISHU_* env is required to download image file_token"}
    meta = MetaClient(meta_access_token, settings.graph_version)
    prepared_urls = []
    staged_photo_ids = []
    for index, file_token in enumerate(file_tokens):
        try:
            content, content_type = await feishu.download_media(file_token)
            staged = await meta.stage_facebook_photo_url(
                account.meta_page_id,
                content,
                filename=f"{record_id}-{index + 1}.png",
                content_type=content_type,
            )
        except (FeishuError, MetaApiError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            return [], {"code": str(code), "message": str(exc)}
        prepared_urls.append(staged["image_url"])
        staged_photo_ids.append(staged["photo_id"])
    update_fields: dict[str, str] = {
        "图片生成状态": "已转发布URL",
        "public_asset_url": "\n".join(prepared_urls),
        "运行/回放ID": run_id,
    }
    if len(prepared_urls) == 1:
        update_fields["主图URL"] = prepared_urls[0]
        update_fields["AI生成图链接"] = prepared_urls[0]
    else:
        update_fields["Carousel素材URL"] = "\n".join(prepared_urls)
    if staged_photo_ids:
        update_fields["FB Staged Photo ID"] = "\n".join(staged_photo_ids)
    client = _feishu(settings)
    if client is not None and record_id != "inline":
        await client.update_record(settings.content_table_id, record_id, update_fields)
    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="assets/prepare",
        status="success",
        input_hash=hashlib.sha256("|".join(file_tokens).encode("utf-8")).hexdigest(),
        output_summary=f"prepared_urls={len(prepared_urls)}; staged_photo_ids={','.join(staged_photo_ids)}",
        decision_reason="file-token-to-meta-cdn",
        mode="commit",
    )
    return prepared_urls, None


async def _execute_publish(req: PublishRequest, *, commit: bool, settings: Settings) -> dict:
    record_id, record = await _load_record(req, settings)
    account = await _load_account(req, record, settings)
    recent = await _load_recent_records(req, settings)
    now = _parse_now(req.now)
    run_id = f"spv1-{uuid.uuid4().hex[:16]}"
    ig_limit = None
    meta_access_token = settings.meta_token_for_brand(account.brand) if account else ""

    if account and account.platform == PLATFORM_INSTAGRAM and meta_access_token:
        try:
            ig_limit = await MetaClient(meta_access_token, settings.graph_version).content_publishing_limit(
                account.ig_user_id
            )
        except MetaApiError as exc:
            ig_limit = {"meta_limit_error": exc.code}

    validation = validate_publish(record, account, recent, now=now, commit=commit, ig_limit=ig_limit)
    mode = "commit" if commit else "dry-run"
    status = "pass" if validation.ok else "blocked"
    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node=f"publish/{mode}",
        status=status,
        input_hash=validation.input_hash,
        output_summary=str(_safe_issues(validation)),
        decision_reason=validation.decision_reason,
        mode=mode,
    )

    if not validation.ok:
        return {"ok": False, "status": "blocked", "run_id": run_id, **_safe_issues(validation)}
    if not commit:
        return {
            "ok": True,
            "status": "dry-run-pass",
            "run_id": run_id,
            "input_hash": validation.input_hash,
            "normalized": validation.normalized,
            **_safe_issues(validation),
        }
    if not settings.commit_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="publish/commit-gate",
            status="blocked",
            input_hash=validation.input_hash,
            output_summary="SOCIAL_PUBLISH_COMMIT_ENABLED=false",
            decision_reason="COMMIT_DISABLED",
            mode="commit",
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "COMMIT_DISABLED"}]}
    if not meta_access_token:
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "META_TOKEN_MISSING"}]}

    assert account is not None
    caption = str(validation.normalized.get("publish_caption") or "").strip()
    asset_urls, prepare_error = await _prepare_asset_urls_for_commit(
        settings=settings,
        account=account,
        record_id=record_id,
        normalized=validation.normalized,
        run_id=run_id,
    )
    if prepare_error:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="assets/prepare",
            status="blocked",
            input_hash=validation.input_hash,
            output_summary=json.dumps(prepare_error, ensure_ascii=False),
            decision_reason=str(prepare_error.get("code", "ASSET_PREPARE_FAILED")),
            mode="commit",
        )
        return {
            "ok": False,
            "status": "blocked",
            "run_id": run_id,
            "blocking": [prepare_error],
        }
    meta = MetaClient(meta_access_token, settings.graph_version)
    try:
        if account.platform == PLATFORM_INSTAGRAM:
            if validation.normalized["material_type"] == "carousel":
                published = await meta.publish_instagram_carousel(account.ig_user_id, asset_urls, caption)
            else:
                published = await meta.publish_instagram_image(account.ig_user_id, asset_urls[0], caption)
            update = {
                "状态": "已发布",
                "实际发布时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "IG Creation ID": published.get("creation_id", ""),
                "IG Media ID": published.get("media_id", ""),
                "平台返回ID": published.get("media_id", ""),
                "前台链接": published.get("permalink", ""),
                "运行/回放ID": run_id,
            }
        elif account.platform == PLATFORM_FACEBOOK:
            published = await meta.publish_facebook_photo(account.meta_page_id, asset_urls[0], caption)
            update = {
                "状态": "已发布",
                "实际发布时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "FB Photo ID": published.get("photo_id", ""),
                "Meta Page Post ID": published.get("post_id", ""),
                "平台返回ID": published.get("post_id") or published.get("photo_id", ""),
                "前台链接": published.get("permalink", ""),
                "运行/回放ID": run_id,
            }
        else:
            raise MetaApiError("Unsupported platform", "UNSUPPORTED_PLATFORM")
    except MetaApiError as exc:
        client = _feishu(settings)
        if client is not None and record_id != "inline":
            await client.update_record(
                settings.content_table_id,
                record_id,
                {"状态": "发布失败", "发布错误码": exc.code, "发布错误信息": str(exc), "运行/回放ID": run_id},
            )
        return {"ok": False, "status": "publish-failed", "run_id": run_id, "error_code": exc.code}

    client = _feishu(settings)
    if client is not None and record_id != "inline":
        await client.update_record(settings.content_table_id, record_id, update)
    return {"ok": True, "status": "published", "run_id": run_id, "platform": account.platform, "result": update}


def _publish_scan_candidate_reason(fields: dict, *, now: datetime, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "candidate-forced"
    status = select_value(fields.get("状态"))
    if status != "待发布":
        return False, f"status={status or 'empty'}"
    mode = select_value(fields.get("发布模式"))
    if mode == "manual":
        return False, "mode=manual"
    if bool_value(fields.get("发布锁")):
        return False, "publish_locked"
    if not bool_value(fields.get("审批通过")):
        return False, "approval_missing"
    if not bool_value(fields.get("最终素材确认")):
        return False, "asset_not_confirmed"
    scheduled_at = parse_dt(fields.get("计划发布时间"))
    if not scheduled_at:
        return False, "schedule_missing"
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at > now + timedelta(minutes=1):
        return False, "schedule_not_due"
    return True, "candidate"


async def _execute_publish_scan(req: PublishScanRequest, settings: Settings) -> dict:
    scan_run_id = f"pscanv1-{uuid.uuid4().hex[:16]}"
    now = _parse_now(req.now)
    records = req.records
    if not records:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        records = await client.list_records(settings.content_table_id, page_size=200)

    selected = []
    skipped = []
    for index, record in enumerate(records):
        fields = normalize_fields(record)
        ok, reason = _publish_scan_candidate_reason(fields, now=now, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, record))
        else:
            skipped.append({"record_id": record_id, "reason": reason})
        if len(selected) >= req.limit:
            break

    results = []
    for record_id, record in selected:
        dry_run = await _execute_publish(
            PublishRequest(record_id=record_id, record=record, recent_records=records, now=req.now),
            commit=False,
            settings=settings,
        )
        item = {"record_id": record_id, "dry_run": dry_run}
        if req.commit and dry_run.get("ok"):
            item["commit"] = await _execute_publish(
                PublishRequest(record_id=record_id, record=record, recent_records=records, now=req.now),
                commit=True,
                settings=settings,
            )
        results.append(item)

    dry_run_passed = sum(1 for item in results if item.get("dry_run", {}).get("ok"))
    committed = sum(1 for item in results if item.get("commit", {}).get("ok"))
    failed = sum(
        1
        for item in results
        if not item.get("dry_run", {}).get("ok") or (req.commit and not item.get("commit", {}).get("ok"))
    )
    summary = {
        "scanned": len(records),
        "selected": len(selected),
        "dry_run_passed": dry_run_passed,
        "committed": committed,
        "failed": failed,
        "commit": req.commit,
        "source": req.source,
        "force": req.force,
        "limit": req.limit,
        "skipped_sample": skipped[:5],
    }
    scan_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    await _write_log(
        settings,
        run_id=scan_run_id,
        record_id="__publish_scan__",
        node="publish/scan",
        status="success" if failed == 0 else "error",
        input_hash=scan_hash,
        output_summary=json.dumps(summary, ensure_ascii=False, default=str),
        decision_reason="publish-scan-complete",
        mode="commit" if req.commit else "dry-run",
        replay_command=(
            'POST /publish/scan '
            f'{{"commit":false,"source":"replay","force":{str(req.force).lower()},"limit":{req.limit}}}'
        ),
    )
    return {
        "ok": failed == 0,
        "status": "publish-scan-complete",
        "scan_run_id": scan_run_id,
        "scanned": len(records),
        "selected": len(selected),
        "dry_run_passed": dry_run_passed,
        "committed": committed,
        "failed": failed,
        "commit": req.commit,
        "results": results,
        "skipped_sample": skipped[:20],
    }


async def _mark_generation_error(settings: Settings, record_id: str, run_id: str, message: str) -> None:
    client = _feishu(settings)
    if client is None or record_id == "inline":
        return
    try:
        await client.update_record(
            settings.content_table_id,
            record_id,
            {
                "AI生成状态": "生成失败",
                "AI生成错误": message[:1000],
                "运行/回放ID": run_id,
            },
        )
    except FeishuError:
        return


async def _execute_generate(req: GenerateBriefRequest, settings: Settings) -> dict:
    run_id = f"genv1-{uuid.uuid4().hex[:16]}"
    record_id, record = await _load_generate_record(req, settings)
    fields = record.get("fields", record)
    fields = await _enrich_content_fields_with_product_context(fields, settings)
    current_hash = generation_input_hash(fields)
    mode = "commit" if req.write_back else "dry-run"

    if req.write_back and not settings.generation_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="generate/writeback-gate",
            status="blocked",
            input_hash=current_hash,
            output_summary="SOCIAL_GENERATION_WRITEBACK_ENABLED=false",
            decision_reason="GENERATION_WRITEBACK_DISABLED",
            mode=mode,
        )
        return {
            "ok": False,
            "status": "blocked",
            "run_id": run_id,
            "blocking": [{"code": "GENERATION_WRITEBACK_DISABLED"}],
        }

    missing = required_generation_missing(fields)
    if missing:
        reason = f"GENERATION_INPUT_MISSING: {', '.join(missing)}"
        if req.write_back:
            await _mark_generation_error(settings, record_id, run_id, reason)
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="generate/brief",
            status="error",
            input_hash=current_hash,
            output_summary=reason,
            decision_reason="GENERATION_INPUT_MISSING",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "missing": missing}

    if (
        not req.force
        and str(fields.get("AI生成输入Hash", "")).strip() == current_hash
        and str(fields.get("AI生成状态", "")).strip() == "已生成"
    ):
        return {"ok": True, "status": "unchanged", "run_id": run_id, "input_hash": current_hash}

    try:
        payload, provider = await generate_payload(fields, settings)
    except Exception as exc:
        reason = f"GENERATION_FAILED: {type(exc).__name__}"
        if req.write_back:
            await _mark_generation_error(settings, record_id, run_id, str(exc))
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="generate/brief",
            status="error",
            input_hash=current_hash,
            output_summary=reason,
            decision_reason=reason,
            mode=mode,
        )
        return {"ok": False, "status": "generation-failed", "run_id": run_id, "reason": reason}

    quality_issues = validate_generation_payload(payload, fields)
    if quality_issues:
        reason = "GENERATION_OUTPUT_INVALID"
        summary = "; ".join(quality_issues)
        if req.write_back:
            await _mark_generation_error(settings, record_id, run_id, f"{reason}: {summary}")
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="generate/brief",
            status="error",
            input_hash=current_hash,
            output_summary=summary,
            decision_reason=reason,
            mode=mode,
        )
        return {
            "ok": False,
            "status": "generation-failed",
            "run_id": run_id,
            "provider": provider,
            "reason": reason,
            "issues": quality_issues,
        }

    try:
        updates = build_update_fields(fields, payload, run_id=run_id, source=req.source)
    except Exception as exc:
        reason = f"GENERATION_FAILED: {type(exc).__name__}"
        if req.write_back:
            await _mark_generation_error(settings, record_id, run_id, str(exc))
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="generate/brief",
            status="error",
            input_hash=current_hash,
            output_summary=reason,
            decision_reason=reason,
            mode=mode,
        )
        return {"ok": False, "status": "generation-failed", "run_id": run_id, "reason": reason}

    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="generate/brief",
        status="success" if req.write_back else "dry-run",
        input_hash=current_hash,
        output_summary=f"provider={provider}; fields={', '.join(sorted(updates.keys()))}",
        decision_reason="generated",
        mode=mode,
    )
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        if record_id == "inline":
            raise HTTPException(status_code=400, detail="write_back requires a persisted record_id")
        await client.update_record(settings.content_table_id, record_id, updates)

    return {
        "ok": True,
        "status": "generated" if req.write_back else "dry-run-generated",
        "run_id": run_id,
        "provider": provider,
        "input_hash": current_hash,
        "fields": updates,
    }


async def _execute_generate_scan(req: GenerateScanRequest, settings: Settings) -> dict:
    scan_run_id = f"gscanv1-{uuid.uuid4().hex[:16]}"
    records = req.records
    if not records:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        records = await client.list_records(settings.content_table_id, page_size=200)

    selected = []
    skipped = []
    for index, record in enumerate(records):
        fields = record.get("fields", record)
        fields = await _enrich_content_fields_with_product_context(fields, settings)
        ok, reason = generation_candidate_reason(fields, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, {"record_id": record_id, "fields": fields}))
        else:
            skipped.append({"record_id": record_id, "reason": reason})
        if len(selected) >= req.limit:
            break

    results = []
    for record_id, record in selected:
        item = await _execute_generate(
            GenerateBriefRequest(
                record_id=record_id,
                record=record,
                write_back=req.write_back,
                source=req.source,
                force=req.force,
            ),
            settings=settings,
        )
        item["record_id"] = record_id
        results.append(item)

    generated = sum(1 for item in results if item.get("ok"))
    failed = sum(1 for item in results if not item.get("ok"))
    summary = {
        "scanned": len(records),
        "selected": len(selected),
        "generated": generated,
        "failed": failed,
        "write_back": req.write_back,
        "source": req.source,
        "force": req.force,
        "limit": req.limit,
        "skipped_sample": skipped[:5],
    }
    scan_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    await _write_log(
        settings,
        run_id=scan_run_id,
        record_id="__scan__",
        node="generate/scan",
        status="success" if failed == 0 else "error",
        input_hash=scan_hash,
        output_summary=json.dumps(summary, ensure_ascii=False, default=str),
        decision_reason="scan-complete",
        mode="commit" if req.write_back else "dry-run",
        replay_command=(
            'POST /generate/scan '
            f'{{"write_back":false,"source":"replay","force":{str(req.force).lower()},"limit":{req.limit}}}'
        ),
    )

    return {
        "ok": all(item.get("ok") for item in results) if results else True,
        "status": "scan-complete",
        "scan_run_id": scan_run_id,
        "scanned": len(records),
        "selected": len(selected),
        "generated": generated,
        "failed": failed,
        "write_back": req.write_back,
        "results": results,
        "skipped_sample": skipped[:20],
    }


async def _execute_approval_action(req: ApprovalActionRequest, settings: Settings) -> dict:
    run_id = f"approv1-{uuid.uuid4().hex[:16]}"
    record_id, record = await _load_approval_record(req, settings)
    fields = normalize_fields(record)
    updates = approval_update_fields(
        req.action,
        fields,
        feedback_text=req.feedback_text,
        copy_overrides=req.copy_overrides,
        feedback_dimensions=req.feedback_dimensions,
        feedback_tags=req.feedback_tags,
        keep=req.keep,
        change=req.change,
        avoid=req.avoid,
    )
    mode = "commit" if req.write_back else "dry-run"
    input_hash = hashlib.sha256(
        json.dumps(
            {
                "action": req.action,
                "record_id": record_id,
                "feedback_text": req.feedback_text,
                "copy_overrides": req.copy_overrides,
                "feedback_dimensions": req.feedback_dimensions,
                "feedback_tags": req.feedback_tags,
                "create_image_task": req.create_image_task,
                "updates": updates,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    if req.write_back and not settings.approval_writeback_enabled:
        await _write_log(
            settings,
            run_id=run_id,
            record_id=record_id,
            node="approval/action-gate",
            status="blocked",
            input_hash=input_hash,
            output_summary="SOCIAL_APPROVAL_WRITEBACK_ENABLED=false",
            decision_reason="APPROVAL_WRITEBACK_DISABLED",
            mode=mode,
        )
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "APPROVAL_WRITEBACK_DISABLED"}], "fields": updates}

    if req.write_back:
        if record_id == "inline":
            raise HTTPException(status_code=400, detail="write_back requires a persisted record_id")
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        await client.update_record(settings.content_table_id, record_id, updates)

    image_task_result = None
    if req.create_image_task and req.action in {"regenerate_image", "regenerate_both"}:
        merged_fields = dict(fields)
        merged_fields.update(updates)
        merged_fields["_approval_regeneration_image_task"] = True
        if _is_carousel_content(merged_fields):
            image_task_result = await _execute_carousel_image_tasks(
                record_id,
                merged_fields,
                write_task=req.write_back,
                settings=settings,
            )
        else:
            image_task_result = await _execute_image_task(
                ImageTaskRequest(
                    record_id=record_id,
                    record={"record_id": record_id, "fields": merged_fields},
                    write_task=req.write_back,
                    source="manual",
                    force=True,
                ),
                settings=settings,
            )

    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="approval/action",
        status="success" if req.write_back else "dry-run",
        input_hash=input_hash,
        output_summary=json.dumps(updates, ensure_ascii=False, default=str),
        decision_reason=req.action,
        mode=mode,
    )
    ok = image_task_result.get("ok") if image_task_result is not None else True
    return {
        "ok": bool(ok),
        "status": "approval-updated" if req.write_back else "approval-dry-run",
        "run_id": run_id,
        "fields": updates,
        "image_task": image_task_result,
    }


async def _execute_social_crm_p1_publish(req: SocialCrmP1PublishRequest, *, commit: bool, settings: Settings) -> dict:
    if not req.record and not req.record_id:
        raise HTTPException(status_code=400, detail="record_id or record is required")
    raw_record = req.record
    if raw_record is None:
        record_id, raw_record = await _load_record(PublishRequest(record_id=req.record_id), settings)
    else:
        record_id = req.record_id or raw_record.get("record_id", "inline-social-crm-p1")
        raw_record = {"record_id": record_id, "fields": raw_record.get("fields", raw_record)}

    publish_req = build_social_crm_p1_publish_request(req, raw_record)
    mapped_fields = normalize_fields(publish_req.record or {})
    precheck = social_crm_p1_precheck(
        req,
        mapped_fields,
        commit=commit,
        p1_publish_enabled=settings.social_crm_p1_publish_enabled,
    )
    if precheck:
        return {
            "ok": False,
            "status": "blocked",
            "mode": "commit" if commit else "dry-run",
            "record_id": publish_req.record_id,
            "blocking": precheck,
            "mapped_fields": mapped_fields,
            "writeback_hint": social_crm_p1_writeback_hint({"ok": False, "status": "blocked", "blocking": precheck}),
        }

    result = await _execute_publish(publish_req, commit=commit, settings=settings)
    result["social_crm_p1"] = {
        "record_id": publish_req.record_id,
        "mode": "commit" if commit else "dry-run",
        "canary": req.canary,
        "source": req.source,
        "mapped_fields": mapped_fields,
        "writeback_hint": social_crm_p1_writeback_hint(result),
    }
    return result


@app.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "ok": True,
        "commit_enabled": settings.commit_enabled,
        "generation_writeback_enabled": settings.generation_writeback_enabled,
        "image_task_write_enabled": settings.image_task_write_enabled,
        "image_result_writeback_enabled": settings.image_result_writeback_enabled,
        "asset_prepare_enabled": settings.asset_prepare_enabled,
        "approval_writeback_enabled": settings.approval_writeback_enabled,
        "plan_writeback_enabled": settings.plan_writeback_enabled,
        "feishu_configured": settings.feishu_enabled(),
        "image_task_configured": settings.image_task_enabled(),
        "product_index_configured": bool(settings.product_index_table_id),
        "kol_candidate_configured": bool(settings.kol_candidate_table_id),
        "meta_configured": settings.meta_enabled(),
        "social_crm_p0_base_configured": settings.social_crm_p0_base_enabled(),
        "social_crm_p0_write_enabled": settings.social_crm_p0_write_enabled,
        "social_crm_p0_youtube_configured": settings.social_crm_p0_youtube_enabled(),
        "social_crm_p0_x_configured": settings.social_crm_p0_x_enabled(),
        "social_crm_p1_publish_configured": settings.social_crm_p1_publish_configured(),
        "social_crm_p1_publish_enabled": settings.social_crm_p1_publish_enabled,
        "social_crm_p1_canary_only": True,
        "generation_ai_provider": settings.generation_ai_provider,
        "generation_ai_configured": settings.generation_ai_enabled(),
    }


@app.post("/social-crm-p0/sync")
async def social_crm_p0_sync(
    req: SocialCrmP0SyncRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await run_social_crm_p0_sync(req, settings)


@app.post("/social-crm-p1/publish/dry-run")
async def social_crm_p1_publish_dry_run(
    req: SocialCrmP1PublishRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_social_crm_p1_publish(req, commit=False, settings=settings)


@app.post("/social-crm-p1/publish/commit")
async def social_crm_p1_publish_commit(
    req: SocialCrmP1PublishRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_social_crm_p1_publish(req, commit=True, settings=settings)


@app.post("/plan/weekly")
async def plan_weekly(
    req: PlanWeeklyRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_plan_weekly(req, settings=settings)


@app.post("/plan/daily-confirm")
async def plan_daily_confirm(
    req: PlanDailyConfirmRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_plan_daily_confirm(req, settings=settings)


@app.post("/plan/reselect")
async def plan_reselect(
    req: PlanReselectRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_plan_reselect(req, settings=settings)


@app.post("/plan/weekly-input-card")
async def plan_weekly_input_card(
    req: WeeklyInputCardRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_weekly_input_card(req, settings=settings)


@app.post("/plan/weekly-input-action")
async def plan_weekly_input_action(
    req: WeeklyInputActionRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_weekly_input_action(req, settings=settings)


@app.post("/plan/product-index/sync")
async def plan_product_index_sync(
    req: ProductIndexSyncRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_product_index_sync(req, settings=settings)


@app.post("/discovery/reference/weekly")
async def discovery_reference_weekly(
    req: ReferenceWeeklyDiscoveryRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_reference_weekly(req, settings=settings)


@app.post("/discovery/kol/weekly")
async def discovery_kol_weekly(
    req: KolWeeklyDiscoveryRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_kol_weekly(req, settings=settings)


@app.post("/discovery/kol/visual-posts")
async def discovery_kol_visual_posts(
    req: KolVisualPostDiscoveryRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_kol_visual_posts(req, settings=settings)


@app.post("/discovery/kol/action")
async def discovery_kol_action(
    req: KolActionRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_kol_action(req, settings=settings)


@app.post("/discovery/reference/action")
async def discovery_reference_action(
    req: ReferenceActionRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_reference_action(req, settings=settings)


@app.post("/generate/brief")
async def generate_brief(
    req: GenerateBriefRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_generate(req, settings=settings)


@app.post("/generate/scan")
async def generate_scan(
    req: GenerateScanRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_generate_scan(req, settings=settings)


@app.post("/image-task/create")
async def image_task_create(
    req: ImageTaskRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_image_task(req, settings=settings)


@app.post("/image-task/scan")
async def image_task_scan(
    req: ImageTaskScanRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_image_task_scan(req, settings=settings)


@app.post("/image-task/ingest")
async def image_task_ingest(
    req: ImageResultIngestRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_image_result_ingest(req, settings=settings)


@app.post("/image-task/ingest-scan")
async def image_task_ingest_scan(
    req: ImageResultIngestScanRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_image_result_ingest_scan(req, settings=settings)


@app.post("/approval/card-preview")
async def approval_card_preview_endpoint(
    req: ApprovalCardPreviewRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    record_id, record = await _load_approval_record(req, settings)
    return {"ok": True, "status": "approval-card-preview", "card": approval_card_preview(record_id, normalize_fields(record))}


@app.post("/approval/action")
async def approval_action(
    req: ApprovalActionRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_approval_action(req, settings=settings)


@app.post("/publish/scan")
async def publish_scan(
    req: PublishScanRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_publish_scan(req, settings=settings)


@app.post("/publish/dry-run")
async def publish_dry_run(
    req: PublishRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_publish(req, commit=False, settings=settings)


@app.post("/publish/commit")
async def publish_commit(
    req: PublishRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    return await _execute_publish(req, commit=True, settings=settings)


@app.post("/replay")
async def replay(
    req: ReplayRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    if not req.record_id and not req.record:
        raise HTTPException(status_code=400, detail="record_id or record is required for v1 replay")
    if req.run_id.startswith("genv1-"):
        if req.mode == "commit":
            raise HTTPException(status_code=400, detail="generation replay only supports dry-run")
        return await _execute_generate(
            GenerateBriefRequest(
                record_id=req.record_id,
                record=req.record,
                write_back=False,
                source="replay",
                force=True,
            ),
            settings=settings,
        )
    if req.run_id.startswith("imgtaskv1-"):
        if req.mode == "commit":
            raise HTTPException(status_code=400, detail="image task replay only supports dry-run")
        return await _execute_image_task(
            ImageTaskRequest(record_id=req.record_id, record=req.record, write_task=False, source="replay", force=True),
            settings=settings,
        )
    if req.run_id.startswith("imgresultv1-"):
        if req.mode == "commit":
            raise HTTPException(status_code=400, detail="image result replay only supports dry-run")
        return await _execute_image_result_ingest(
            ImageResultIngestRequest(record_id=req.record_id, record=req.record, write_back=False, source="replay"),
            settings=settings,
        )
    if req.run_id.startswith("spv1-"):
        publish_req = PublishRequest(record_id=req.record_id, record=req.record)
        return await _execute_publish(publish_req, commit=(req.mode == "commit"), settings=settings)
    raise HTTPException(status_code=400, detail="unknown replay run_id prefix")


@app.post("/insights/poll")
async def insights_poll(
    req: InsightsPollRequest,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
):
    _check_auth(settings, authorization)
    if not settings.meta_enabled():
        return {"ok": False, "status": "blocked", "reason": "META_TOKEN_MISSING"}
    record = req.record
    if not record:
        if not req.record_id:
            raise HTTPException(status_code=400, detail="record_id or record is required")
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        record = await client.get_record(settings.content_table_id, req.record_id)
    fields = record.get("fields", record)
    media_id = _platform_id(fields.get("IG Media ID"))
    post_id = (
        _platform_id(fields.get("Meta Page Post ID"))
        or _platform_id(fields.get("FB Photo ID"))
        or _platform_id(fields.get("平台返回ID"))
    )
    brand = select_value(fields.get("品牌")) or text_value(fields.get("品牌"))
    meta_access_token = settings.meta_token_for_brand(brand)
    if not meta_access_token:
        return {"ok": False, "status": "blocked", "reason": "META_TOKEN_MISSING"}
    meta = MetaClient(meta_access_token, settings.graph_version)
    try:
        if media_id:
            data = await meta.ig_media_insights(media_id)
        elif post_id:
            data = await meta.fb_post_insights(post_id)
        else:
            return {"ok": False, "status": "blocked", "reason": "PLATFORM_ID_MISSING"}
    except MetaApiError as exc:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "META_INSIGHTS_FAILED",
            "error_code": exc.code,
            "message": str(exc),
        }
    return {"ok": True, "status": "insights-fetched", "window": req.window, "data": data}

