from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from typing import Any

import httpx


class FeishuError(RuntimeError):
    pass


DATETIME_FIELD_NAMES = {
    "发生时间",
    "AI生成时间",
    "审批通过时间",
    "计划发布时间",
    "计划日期",
    "日确认时间",
    "实际发布时间",
}

NUMBER_FIELD_NAMES = {
    "重推次数",
    "重生版本号",
}

URL_FIELD_NAMES = {
    "AI生成图链接",
    "public_asset_url",
    "素材链接",
    "目标链接",
    "前台链接",
    "主图URL",
    "账号链接",
    "账号/帖子URL",
    "样例帖子1链接",
    "样例帖子2链接",
    "样例帖子1图片",
    "样例帖子2图片",
    "视觉参考缩略图",
    "样例图片链接",
}


def _datetime_to_ms(value: Any) -> Any:
    if value in ("", None):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return value
        if raw.isdigit():
            number = int(raw)
            return number if number > 10_000_000_000 else number * 1000
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M")
                except ValueError:
                    return value
    else:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _url_text_to_link(value: Any) -> Any:
    if value in ("", None):
        return value
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw.lower().startswith(("http://", "https://")):
        return value
    first_url = raw.splitlines()[0].strip()
    return {"link": first_url, "text": raw}


def _number_to_number(value: Any) -> Any:
    if value in ("", None):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return value
        try:
            parsed = float(raw)
        except ValueError:
            return value
        return int(parsed) if parsed.is_integer() else parsed
    return value


def _normalize_bitable_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fields)
    for name in DATETIME_FIELD_NAMES:
        if name in normalized:
            normalized[name] = _datetime_to_ms(normalized[name])
    for name in NUMBER_FIELD_NAMES:
        if name in normalized:
            normalized[name] = _number_to_number(normalized[name])
    for name in URL_FIELD_NAMES:
        if name in normalized:
            if normalized[name] in ("", None):
                normalized.pop(name)
            else:
                normalized[name] = _url_text_to_link(normalized[name])
    return normalized


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, base_token: str):
        if not (app_id and app_secret and base_token):
            raise FeishuError("Feishu credentials are not configured")
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_token = base_token
        self._token: str | None = None
        self._base = "https://open.feishu.cn/open-apis"

    async def _tenant_token(self) -> str:
        if self._token:
            return self._token
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"tenant token error: {data.get('code')} {data.get('msg')}")
        self._token = data["tenant_access_token"]
        return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        token = await self._tenant_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, f"{self._base}{path}", headers=headers, **kwargs)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuError(f"Feishu API error: HTTP {resp.status_code}, code={data.get('code')}, msg={data.get('msg')}")
        return data

    async def download_media(self, file_token: str) -> tuple[bytes, str]:
        token = await self._tenant_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                f"{self._base}/drive/v1/medias/{file_token}/download",
                headers=headers,
            )
        if resp.status_code >= 400:
            raise FeishuError(f"Feishu media download error: HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip() or "image/png"
        return resp.content, content_type

    async def upload_bitable_media(self, *, file_name: str, content: bytes, content_type: str = "image/png") -> str:
        token = await self._tenant_token()
        headers = {"Authorization": f"Bearer {token}"}
        safe_name = file_name.strip() or "product_reference.png"
        guessed = mimetypes.guess_type(safe_name)[0]
        media_type = content_type or guessed or "application/octet-stream"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base}/drive/v1/medias/upload_all",
                headers=headers,
                data={
                    "file_name": safe_name,
                    "parent_type": "bitable_file",
                    "parent_node": self.base_token,
                    "size": str(len(content)),
                },
                files={"file": (safe_name, content, media_type)},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuError(f"Feishu media upload error: HTTP {resp.status_code}, code={data.get('code')}, msg={data.get('msg')}")
        file_token = data.get("data", {}).get("file_token")
        if not file_token:
            raise FeishuError("Feishu media upload error: missing file_token")
        return file_token

    async def upload_message_image(self, *, file_name: str, content: bytes, content_type: str = "image/png") -> str:
        token = await self._tenant_token()
        headers = {"Authorization": f"Bearer {token}"}
        safe_name = file_name.strip() or "reference_preview.png"
        guessed = mimetypes.guess_type(safe_name)[0]
        media_type = content_type or guessed or "application/octet-stream"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base}/im/v1/images",
                headers=headers,
                data={"image_type": "message"},
                files={"image": (safe_name, content, media_type)},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code") not in (0, None):
            raise FeishuError(f"Feishu image upload error: HTTP {resp.status_code}, code={data.get('code')}, msg={data.get('msg')}")
        image_key = data.get("data", {}).get("image_key")
        if not image_key:
            raise FeishuError("Feishu image upload error: missing image_key")
        return image_key

    async def upload_message_image_from_url(self, image_url: str, *, file_name: str = "reference_preview.png") -> str:
        raw = image_url.strip()
        if not raw.lower().startswith(("http://", "https://")):
            raise FeishuError("Feishu image upload error: image_url must be http(s)")
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(raw)
        if resp.status_code >= 400:
            raise FeishuError(f"Feishu image upload error: source image HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip() or "image/png"
        if not content_type.startswith("image/"):
            raise FeishuError(f"Feishu image upload error: source content-type is {content_type}")
        if len(resp.content) > 10 * 1024 * 1024:
            raise FeishuError("Feishu image upload error: source image exceeds 10MB")
        return await self.upload_message_image(file_name=file_name, content=resp.content, content_type=content_type)

    async def get_record(self, table_id: str, record_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/{record_id}",
        )
        return data["data"]["record"]

    async def list_records(self, table_id: str, page_size: int = 200) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = await self._request(
                "GET",
                f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records",
                params=params,
            )
            body = data.get("data", {})
            records.extend(body.get("items", []))
            if not body.get("has_more"):
                return records
            page_token = body.get("page_token", "")

    async def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> dict:
        data = await self._request(
            "PUT",
            f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/{record_id}",
            json={"fields": _normalize_bitable_fields(fields)},
        )
        return data.get("data", {})

    async def create_record(self, table_id: str, fields: dict[str, Any]) -> dict:
        data = await self._request(
            "POST",
            f"/bitable/v1/apps/{self.base_token}/tables/{table_id}/records",
            json={"fields": _normalize_bitable_fields(fields)},
        )
        return data.get("data", {})

    async def find_account(self, account_table_id: str, account_name: str) -> dict[str, Any] | None:
        for record in await self.list_records(account_table_id):
            fields = record.get("fields", {})
            if str(fields.get("账号名称", "")).strip() == account_name:
                return record
        return None

    async def append_run_log(
        self,
        log_table_id: str,
        *,
        run_id: str,
        record_id: str,
        node: str,
        status: str,
        input_hash: str,
        output_summary: str,
        decision_reason: str,
        mode: str,
        retry_count: int = 0,
        replay_command: str | None = None,
    ) -> None:
        if replay_command is None:
            replay_command = f"POST /replay {{\"run_id\":\"{run_id}\",\"record_id\":\"{record_id}\",\"mode\":\"dry-run\"}}"
        if node.startswith("generate/"):
            module = "内容Brief"
        elif node.startswith("image-task/") or node.startswith("assets/"):
            module = "图片生成"
        else:
            module = "发布审核"
        await self.create_record(
            log_table_id,
            {
                "发生时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "运行标题": f"{node} · {record_id}",
                "业务模块": module,
                "运行状态": status if status in {"success", "warning", "error", "dry-run"} else ("success" if status == "pass" else "error"),
                "关联记录ID": record_id,
                "run_id": run_id,
                "record_id": record_id,
                "input_hash": input_hash,
                "output_summary": output_summary[:1000],
                "decision_reason": decision_reason[:1000],
                "dry_run/commit": mode,
                "retry_count": retry_count,
                "可回放命令": replay_command,
                "输入摘要": input_hash,
                "输出摘要": output_summary[:1000],
                "决策依据": decision_reason[:1000],
                "Replay参数": replay_command,
            },
        )
