from __future__ import annotations

import argparse
import json
import os
import pathlib
import urllib.error
import urllib.request
from typing import Any


FIXTURE_FIELDS = {
    "内容标题": "Powkong setup angle",
    "产品名": "Powkong Switch 2 Dock",
    "品牌型号/SKU": "PK01A-01",
    "主推卖点": "compact display-friendly charging",
    "品牌": "Powkong",
    "平台": ["Instagram"],
    "发布位置": ["IG Feed"],
    "内容支柱": "产品场景",
    "目标信号": ["Saves"],
    "实验变量": "Hook",
    "素材类型": "single_image",
    "状态": "选题中",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only remote readiness check for FB/IG content generation."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("SOCIAL_PUBLISH_SERVICE_URL", ""),
        help="Service base URL. Defaults to SOCIAL_PUBLISH_SERVICE_URL.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SOCIAL_PUBLISH_API_TOKEN", ""),
        help="Bearer token. Defaults to SOCIAL_PUBLISH_API_TOKEN. Value is never printed.",
    )
    parser.add_argument(
        "--expect-writeback-enabled",
        action="store_true",
        help="Expect SOCIAL_GENERATION_WRITEBACK_ENABLED=true. Default expects it to be false.",
    )
    parser.add_argument(
        "--allow-publish-commit-enabled",
        action="store_true",
        help="Allow SOCIAL_PUBLISH_COMMIT_ENABLED=true. Default requires it to be false.",
    )
    parser.add_argument(
        "--expect-ai-configured",
        action="store_true",
        help="Require live AI generation config instead of template fallback.",
    )
    parser.add_argument(
        "--allow-feishu-unconfigured",
        action="store_true",
        help="Allow FEISHU_* env to be absent. Default requires Feishu config because /generate/scan cron reads Base.",
    )
    parser.add_argument(
        "--expect-meta-configured",
        action="store_true",
        help="Require Meta API config. Not needed for generation-only readiness, useful before publish dry-run/commit observation.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional JSON report path. The report never includes the bearer token.",
    )
    return parser.parse_args()


def request_json(
    method: str,
    base_url: str,
    path: str,
    *,
    token: str = "",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    endpoint = base_url.rstrip("/") + path
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": raw}
        return exc.code, data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_health(args: argparse.Namespace) -> dict[str, Any]:
    status, data = request_json("GET", args.url, "/health")
    require(status == 200, f"/health expected HTTP 200, got {status}: {data}")
    require(data.get("ok") is True, f"/health returned ok!=true: {data}")
    if not args.allow_publish_commit_enabled:
        require(data.get("commit_enabled") is False, "SOCIAL_PUBLISH_COMMIT_ENABLED must remain false")
    require(data.get("image_task_write_enabled") is False, "SOCIAL_IMAGE_TASK_WRITE_ENABLED must remain false")
    require(data.get("image_result_writeback_enabled") is False, "SOCIAL_IMAGE_RESULT_WRITEBACK_ENABLED must remain false")
    require(data.get("asset_prepare_enabled") is False, "SOCIAL_ASSET_PREPARE_ENABLED must remain false")
    if not args.allow_feishu_unconfigured:
        require(data.get("feishu_configured") is True, "FEISHU_* env must be configured for service-side Base scan")
    if args.expect_meta_configured:
        require(data.get("meta_configured") is True, "META_ACCESS_TOKEN must be configured")
    expected_writeback = bool(args.expect_writeback_enabled)
    require(
        data.get("generation_writeback_enabled") is expected_writeback,
        f"generation_writeback_enabled expected {expected_writeback}, got {data.get('generation_writeback_enabled')}",
    )
    if args.expect_ai_configured:
        require(data.get("generation_ai_configured") is True, "GENERATION_AI_API_KEY/provider must be configured")
        require(data.get("generation_ai_provider") != "template", "GENERATION_AI_PROVIDER must not be template")
    return data


def check_inline_generation(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "write_back": False,
        "source": "manual",
        "force": True,
        "limit": 10,
        "records": [
            {"record_id": "readiness_rec_ok", "fields": FIXTURE_FIELDS},
            {"record_id": "readiness_rec_skip", "fields": {**FIXTURE_FIELDS, "状态": "待发布"}},
        ],
    }
    status, data = request_json("POST", args.url, "/generate/scan", token=args.token, payload=payload)
    require(status == 200, f"/generate/scan expected HTTP 200, got {status}: {data}")
    require(data.get("ok") is True, f"/generate/scan returned ok!=true: {data}")
    require(data.get("status") == "scan-complete", f"expected scan-complete: {data}")
    require(str(data.get("scan_run_id", "")).startswith("gscanv1-"), f"missing gscanv1 scan_run_id: {data}")
    require(data.get("selected") == 1, f"expected one selected record: {data}")
    require(data.get("generated") == 1, f"expected one generated record: {data}")
    first = (data.get("results") or [{}])[0]
    require(first.get("record_id") == "readiness_rec_ok", f"missing result record_id readiness_rec_ok: {data}")
    fields = first.get("fields") or {}
    for required in ("Caption EN", "AI图片Prompt", "发布Checklist", "风险Checklist"):
        require(bool(fields.get(required)), f"missing generated field {required}: {data}")
    return data


def check_writeback_gate_disabled(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.expect_writeback_enabled:
        return None
    payload = {
        "record_id": "readiness_inline_gate",
        "record": {"fields": FIXTURE_FIELDS},
        "write_back": True,
        "source": "manual",
        "force": True,
    }
    status, data = request_json("POST", args.url, "/generate/brief", token=args.token, payload=payload)
    require(status == 200, f"/generate/brief gate check expected HTTP 200, got {status}: {data}")
    require(data.get("ok") is False, f"writeback gate check should be blocked: {data}")
    require(data.get("status") == "blocked", f"writeback gate status should be blocked: {data}")
    blocking = data.get("blocking") or []
    codes = {item.get("code") for item in blocking if isinstance(item, dict)}
    require("GENERATION_WRITEBACK_DISABLED" in codes, f"missing GENERATION_WRITEBACK_DISABLED: {data}")
    require("fields" not in data, f"blocked writeback check must not return generated fields: {data}")
    return data


def check_inline_image_task_scan(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "write_task": False,
        "source": "manual",
        "force": True,
        "limit": 10,
        "records": [
            {
                "record_id": "readiness_img_ok",
                "fields": {
                    **FIXTURE_FIELDS,
                    "状态": "待审核",
                    "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                    "图片生成模式": "Codex Image",
                    "图片生成状态": "待生成",
                },
            }
        ],
    }
    status, data = request_json("POST", args.url, "/image-task/scan", token=args.token, payload=payload)
    require(status == 200, f"/image-task/scan expected HTTP 200, got {status}: {data}")
    require(data.get("ok") is True, f"/image-task/scan returned ok!=true: {data}")
    require(data.get("status") == "image-scan-complete", f"expected image-scan-complete: {data}")
    require(str(data.get("scan_run_id", "")).startswith("imgscanv1-"), f"missing imgscanv1 scan_run_id: {data}")
    require(data.get("selected") == 1, f"expected one selected image record: {data}")
    first = (data.get("results") or [{}])[0]
    require(first.get("record_id") == "readiness_img_ok", f"missing image result record_id readiness_img_ok: {data}")
    task_fields = first.get("task_fields") or {}
    require(task_fields.get("工作流选择") == "Codex Image", f"image task payload missing Codex Image route: {data}")
    return data


def main() -> int:
    args = parse_args()
    require(bool(args.url.strip()), "--url or SOCIAL_PUBLISH_SERVICE_URL is required")
    health = check_health(args)
    scan = check_inline_generation(args)
    image_scan = check_inline_image_task_scan(args)
    gate = check_writeback_gate_disabled(args)
    report = {
        "ok": True,
        "service": args.url.rstrip("/"),
        "checks": {
            "health": True,
            "inline_generate_scan": True,
            "inline_image_task_scan": True,
            "writeback_gate_checked": gate is not None,
            "publish_commit_gate_safe": health.get("commit_enabled") is False or args.allow_publish_commit_enabled,
            "image_task_write_gate_safe": health.get("image_task_write_enabled") is False,
            "image_result_writeback_gate_safe": health.get("image_result_writeback_enabled") is False,
            "asset_prepare_gate_safe": health.get("asset_prepare_enabled") is False,
            "generation_writeback_expected": bool(args.expect_writeback_enabled),
            "feishu_required": not args.allow_feishu_unconfigured,
            "meta_required": bool(args.expect_meta_configured),
            "ai_required": bool(args.expect_ai_configured),
        },
        "commit_enabled": health.get("commit_enabled"),
        "generation_writeback_enabled": health.get("generation_writeback_enabled"),
        "image_task_write_enabled": health.get("image_task_write_enabled"),
        "image_result_writeback_enabled": health.get("image_result_writeback_enabled"),
        "asset_prepare_enabled": health.get("asset_prepare_enabled"),
        "feishu_configured": health.get("feishu_configured"),
        "image_task_configured": health.get("image_task_configured"),
        "meta_configured": health.get("meta_configured"),
        "generation_ai_provider": health.get("generation_ai_provider"),
        "generation_ai_configured": health.get("generation_ai_configured"),
        "scan_run_id": scan.get("scan_run_id"),
        "first_run_id": (scan.get("results") or [{}])[0].get("run_id"),
        "image_scan_run_id": image_scan.get("scan_run_id"),
        "first_image_run_id": (image_scan.get("results") or [{}])[0].get("run_id"),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path:
        path = pathlib.Path(args.report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
