from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


class FeishuError(RuntimeError):
    pass


DATETIME_FIELD_NAMES = {
    "发生时间",
    "AI生成时间",
    "审批通过时间",
    "计划发布时间",
    "实际发布时间",
}

URL_FIELD_NAMES = {
    "AI生成图链接",
    "public_asset_url",
    "素材链接",
    "目标链接",
    "前台链接",
    "主图URL",
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


def _normalize_bitable_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fields)
    for name in DATETIME_FIELD_NAMES:
        if name in normalized:
            normalized[name] = _datetime_to_ms(normalized[name])
    for name in URL_FIELD_NAMES:
        if name in normalized:
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
