from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException

from .config import Settings, get_settings
from .feishu_client import FeishuClient, FeishuError
from .generation import (
    build_update_fields,
    generate_payload,
    generation_candidate_reason,
    generation_input_hash,
    required_generation_missing,
    validate_generation_payload,
)
from .meta_client import MetaApiError, MetaClient
from .models import GenerateBriefRequest, GenerateScanRequest, InsightsPollRequest, PublishRequest, ReplayRequest
from .models import ImageResultIngestRequest, ImageTaskRequest, ImageTaskScanRequest
from .rules import (
    AccountConfig,
    MODE_AUTO,
    PLATFORM_FACEBOOK,
    PLATFORM_INSTAGRAM,
    collect_single_asset_file_tokens,
    validate_publish,
    normalize_fields,
    select_value,
    text_value,
)


app = FastAPI(title="social-publish-service", version="0.1.0")


def _check_auth(settings: Settings, authorization: str | None) -> None:
    if not settings.service_token:
        return
    expected = f"Bearer {settings.service_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


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
    return FeishuClient(settings.feishu_app_id, settings.feishu_app_secret, settings.feishu_base_token)


def _feishu_image(settings: Settings) -> FeishuClient | None:
    if not settings.image_task_enabled():
        return None
    return FeishuClient(settings.feishu_app_id, settings.feishu_app_secret, settings.image_task_base_token)


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
    image_prompt = text_value(fields.get("AI图片Prompt"))
    mode = select_value(fields.get("图片生成模式")) or "Codex Image"
    scene_template = select_value(fields.get("场景模板")) or "FB/INS广告图"
    size = text_value(fields.get("图片生成尺寸")) or _image_ratio_for_content(fields)
    missing = []
    if mode != "Codex Image":
        missing.append("图片生成模式 must be Codex Image")
    if not product:
        missing.append("产品名 or 内容标题")
    if not image_prompt:
        missing.append("AI图片Prompt")
    if text_value(fields.get("图片任务record_id")) and not bool(fields.get("_force_image_task")):
        missing.append("图片任务record_id already exists")

    custom_prompt = "\n".join(
        [
            f"Source content_record_id: {record_id}",
            "Use case: FB/IG organic content candidate image.",
            "Generate a review candidate only. Do not add visible text, logos, watermarks, or copyrighted characters.",
            image_prompt,
        ]
    )
    task_fields = {
        "产品名称": product,
        "品牌": brand,
        "工作流选择": "Codex Image",
        "状态": "待处理",
        "尺寸选择": size,
        "场景模板": scene_template,
        "自定义提示词": custom_prompt,
    }
    raw = json.dumps(task_fields, ensure_ascii=False, sort_keys=True, default=str)
    return task_fields, missing, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _image_task_candidate_reason(fields: dict, *, force: bool = False) -> tuple[bool, str]:
    status = select_value(fields.get("状态"))
    if status not in {"待审核", "选题中"} and not force:
        return False, f"status={status or 'empty'}"
    mode = select_value(fields.get("图片生成模式")) or "Codex Image"
    if mode != "Codex Image":
        return False, f"image_mode={mode or 'empty'}"
    if not text_value(fields.get("AI图片Prompt")):
        return False, "AI图片Prompt missing"
    task_status = select_value(fields.get("图片生成状态"))
    if task_status in {"已提交", "生成中", "已生成-待转URL", "已转发布URL"} and not force:
        return False, f"image_status={task_status}"
    if text_value(fields.get("图片任务record_id")) and not force:
        return False, "image_task_record_id exists"
    return True, "candidate"


def _extract_image_result_fields(image_task_record_id: str, image_task_fields: dict) -> tuple[dict, list[str]]:
    status = select_value(image_task_fields.get("状态"))
    file_token = text_value(image_task_fields.get("生成图片file_token"))
    public_url = text_value(image_task_fields.get("public_asset_url")) or text_value(image_task_fields.get("生成图片URL"))
    location = text_value(image_task_fields.get("生成图片位置"))
    error_message = text_value(image_task_fields.get("错误信息"))
    blocking = []
    if status not in {"处理成功", "已完成", "success"}:
        blocking.append(f"IMAGE_TASK_NOT_DONE:{status or 'empty'}")
    if not (file_token or public_url or location):
        blocking.append("IMAGE_RESULT_MISSING")

    image_status = "已生成"
    if file_token and not public_url:
        image_status = "已生成-待转URL"
    if public_url:
        image_status = "已转发布URL"
    updates = {
        "图片任务record_id": image_task_record_id,
        "图片生成状态": image_status,
        "AI生成图链接": public_url or location,
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
    created = await image_client.create_record(settings.image_task_table_id, task_fields)
    image_task_record_id = text_value(created.get("record", {}).get("record_id")) or text_value(created.get("record_id"))
    if record_id != "inline" and image_task_record_id:
        await content_client.update_record(
            settings.content_table_id,
            record_id,
            {
                "图片生成模式": "Codex Image",
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
        ok, reason = _image_task_candidate_reason(fields, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, record))
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


async def _execute_image_result_ingest(req: ImageResultIngestRequest, settings: Settings) -> dict:
    run_id = f"imgresultv1-{uuid.uuid4().hex[:16]}"
    record_id, record = await _load_image_result_source(req, settings)
    fields = normalize_fields(record)
    image_task_record_id, image_task_record = await _load_image_task_record(req, settings, fields)
    image_task_fields = normalize_fields(image_task_record)
    updates, blocking = _extract_image_result_fields(image_task_record_id, image_task_fields)
    result_hash = hashlib.sha256(json.dumps(updates, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    mode = "commit" if req.write_back else "dry-run"

    await _write_log(
        settings,
        run_id=run_id,
        record_id=record_id,
        node="image-task/ingest",
        status="error" if blocking else ("success" if req.write_back else "dry-run"),
        input_hash=result_hash,
        output_summary=json.dumps({"updates": updates, "blocking": blocking}, ensure_ascii=False),
        decision_reason="; ".join(blocking) if blocking else "image-result-ready",
        mode=mode,
    )
    if blocking:
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": item} for item in blocking]}
    if req.write_back and not settings.image_result_writeback_enabled:
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "IMAGE_RESULT_WRITEBACK_DISABLED"}]}
    if req.write_back:
        client = _feishu(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Feishu env is not configured")
        if record_id == "inline":
            raise HTTPException(status_code=400, detail="write_back requires a persisted record_id")
        await client.update_record(settings.content_table_id, record_id, updates | {"运行/回放ID": run_id})
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
    if not settings.meta_enabled():
        return [], {"code": "META_TOKEN_MISSING", "message": "META_ACCESS_TOKEN is required to prepare asset URLs"}
    if not account.meta_page_id:
        return [], {"code": "META_PAGE_ID_MISSING", "message": "Meta Page ID is required to stage images"}
    feishu = _feishu(settings)
    if feishu is None:
        return [], {"code": "FEISHU_NOT_CONFIGURED", "message": "FEISHU_* env is required to download image file_token"}
    meta = MetaClient(settings.meta_access_token, settings.graph_version)
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

    if account and account.platform == PLATFORM_INSTAGRAM and settings.meta_enabled():
        try:
            ig_limit = await MetaClient(settings.meta_access_token, settings.graph_version).content_publishing_limit(
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
    if not settings.meta_enabled():
        return {"ok": False, "status": "blocked", "run_id": run_id, "blocking": [{"code": "META_TOKEN_MISSING"}]}

    assert account is not None
    fields = record.get("fields", record)
    caption = str(fields.get("Caption EN", "")).strip()
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
    meta = MetaClient(settings.meta_access_token, settings.graph_version)
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

    quality_issues = validate_generation_payload(payload)
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
        ok, reason = generation_candidate_reason(fields, force=req.force)
        record_id = record.get("record_id") or f"inline-{index}"
        if ok:
            selected.append((record_id, record))
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


@app.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "ok": True,
        "commit_enabled": settings.commit_enabled,
        "generation_writeback_enabled": settings.generation_writeback_enabled,
        "image_task_write_enabled": settings.image_task_write_enabled,
        "image_result_writeback_enabled": settings.image_result_writeback_enabled,
        "asset_prepare_enabled": settings.asset_prepare_enabled,
        "feishu_configured": settings.feishu_enabled(),
        "image_task_configured": settings.image_task_enabled(),
        "meta_configured": settings.meta_enabled(),
        "generation_ai_provider": settings.generation_ai_provider,
        "generation_ai_configured": settings.generation_ai_enabled(),
    }


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
    media_id = str(fields.get("IG Media ID", "")).strip()
    post_id = str(fields.get("Meta Page Post ID", "")).strip()
    meta = MetaClient(settings.meta_access_token, settings.graph_version)
    if media_id:
        data = await meta.ig_media_insights(media_id)
    elif post_id:
        data = await meta.fb_post_insights(post_id)
    else:
        return {"ok": False, "status": "blocked", "reason": "PLATFORM_ID_MISSING"}
    return {"ok": True, "status": "insights-fetched", "window": req.window, "data": data}
