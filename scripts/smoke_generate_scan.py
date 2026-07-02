from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
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
    "产品参考图": [{"file_token": "smoke_ref_token", "name": "reference.png"}],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test /generate/scan without touching production by default."
    )
    parser.add_argument(
        "--url",
        default="",
        help="Service base URL. Omit to run in-process with FastAPI TestClient.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SOCIAL_PUBLISH_API_TOKEN", ""),
        help="Bearer token. Defaults to SOCIAL_PUBLISH_API_TOKEN. Value is never printed.",
    )
    parser.add_argument(
        "--record-id",
        default="",
        help="Persisted Feishu record_id to let the service load a real record. Omit for inline fixture.",
    )
    parser.add_argument(
        "--write-back",
        action="store_true",
        help="Write generated fields back to Feishu. Requires --record-id and SOCIAL_GENERATION_WRITEBACK_ENABLED=true on the service.",
    )
    parser.add_argument("--force", action="store_true", help="Force regeneration.")
    parser.add_argument("--limit", type=int, default=10, help="Scan limit for the endpoint.")
    return parser.parse_args()


def build_request(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool]:
    if args.write_back and not args.record_id:
        raise SystemExit("--write-back requires --record-id so inline fixtures are never written.")
    if args.record_id:
        return (
            "/generate/brief",
            {
                "record_id": args.record_id,
                "write_back": args.write_back,
                "source": "manual",
                "force": args.force,
            },
            True,
        )
    payload: dict[str, Any] = {
        "write_back": False,
        "source": "auto",
        "force": args.force,
        "limit": args.limit,
        "records": [
            {"record_id": "smoke_rec_ok", "fields": FIXTURE_FIELDS},
            {"record_id": "smoke_rec_skip", "fields": {**FIXTURE_FIELDS, "状态": "待发布"}},
        ],
    }
    return "/generate/scan", payload, False


def post_remote(url: str, path: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    endpoint = url.rstrip("/") + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
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


def post_in_process(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    service_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(service_root))
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).post(path, json=payload)
    return response.status_code, response.json()


def assert_smoke_response(status_code: int, data: dict[str, Any], *, path: str, persisted_record: bool) -> None:
    if status_code != 200:
        raise AssertionError(f"expected HTTP 200, got {status_code}: {data}")
    if not data.get("ok"):
        raise AssertionError(f"generation returned ok=false: {data}")
    if path == "/generate/scan":
        if data.get("status") != "scan-complete":
            raise AssertionError(f"expected scan-complete, got {data.get('status')}: {data}")
        if not str(data.get("scan_run_id", "")).startswith("gscanv1-"):
            raise AssertionError(f"expected gscanv1 scan_run_id: {data}")
        if data.get("selected", 0) < 1:
            raise AssertionError(f"expected at least one selected record: {data}")
        if data.get("generated", 0) < 1:
            raise AssertionError(f"expected at least one generated record: {data}")
        first = (data.get("results") or [{}])[0]
        if first.get("record_id") != "smoke_rec_ok":
            raise AssertionError(f"expected first result record_id=smoke_rec_ok: {data}")
        fields = first.get("fields") or {}
    else:
        if data.get("status") not in {"generated", "dry-run-generated", "unchanged"}:
            raise AssertionError(f"expected generated status, got {data.get('status')}: {data}")
        fields = data.get("fields") or {}
    if not persisted_record or fields:
        for required in ("Caption EN", "AI图片Prompt", "发布Checklist", "风险Checklist"):
            if not fields.get(required):
                raise AssertionError(f"missing generated field {required}: {data}")


def main() -> int:
    args = parse_args()
    path, payload, persisted_record = build_request(args)
    if args.url:
        status_code, data = post_remote(args.url, path, args.token, payload)
    else:
        status_code, data = post_in_process(path, payload)
    assert_smoke_response(status_code, data, path=path, persisted_record=persisted_record)
    selected = data.get("selected", 1 if data.get("ok") else 0)
    generated = data.get("generated", 1 if data.get("ok") else 0)
    failed = data.get("failed", 0 if data.get("ok") else 1)
    first_run_id = (
        (data.get("results") or [{}])[0].get("run_id")
        if path == "/generate/scan"
        else data.get("run_id")
    )
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "remote" if args.url else "in-process",
                "endpoint": path,
                "write_back": args.write_back,
                "selected": selected,
                "generated": generated,
                "failed": failed,
                "scan_run_id": data.get("scan_run_id"),
                "first_run_id": first_run_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
