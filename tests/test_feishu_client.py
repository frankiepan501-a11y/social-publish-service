import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.feishu_client import FeishuClient, _json_response, _normalize_bitable_fields


class FakeAsyncClient:
    responses = []
    request_count = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def request(self, *args, **kwargs):
        FakeAsyncClient.request_count += 1
        return FakeAsyncClient.responses.pop(0)


class FeishuClientTests(unittest.IsolatedAsyncioTestCase):
    def test_json_response_decodes_feishu_utf8_without_charset(self):
        response = httpx.Response(
            200,
            content='{"code":0,"data":{"record":{"fields":{"状态":"待处理"}}}}'.encode("utf-8"),
        )

        data = _json_response(response)

        self.assertEqual(data["data"]["record"]["fields"]["状态"], "待处理")

    async def test_request_retries_feishu_data_not_ready_for_get(self):
        FakeAsyncClient.responses = [
            httpx.Response(400, content=b'{"code":1254607,"msg":"Data not ready"}'),
            httpx.Response(200, content='{"code":0,"data":{"ok":true}}'.encode("utf-8")),
        ]
        FakeAsyncClient.request_count = 0
        client = FeishuClient("app", "secret", "base")
        client._token = "tenant"

        with (
            patch("app.feishu_client.httpx.AsyncClient", FakeAsyncClient),
            patch("app.feishu_client.asyncio.sleep", AsyncMock()) as sleep_mock,
        ):
            data = await client._request("GET", "/bitable/v1/apps/base/tables/table/records/rec")

        self.assertEqual(data["data"]["ok"], True)
        self.assertEqual(FakeAsyncClient.request_count, 2)
        sleep_mock.assert_awaited_once()

    def test_normalize_bitable_fields_converts_datetime_strings_to_ms(self):
        fields = {
            "AI生成时间": "2026-07-02 04:46:52",
            "发生时间": "2026-07-02 04:46",
            "计划发布时间": "1784073600000",
            "Caption EN": "hello",
        }

        normalized = _normalize_bitable_fields(fields)

        self.assertEqual(normalized["AI生成时间"], 1782967612000)
        self.assertEqual(normalized["发生时间"], 1782967560000)
        self.assertEqual(normalized["计划发布时间"], 1784073600000)
        self.assertEqual(normalized["Caption EN"], "hello")
        self.assertEqual(fields["AI生成时间"], "2026-07-02 04:46:52")

    def test_normalize_bitable_fields_converts_number_strings(self):
        fields = {
            "重推次数": "0",
            "重生版本号": "2.5",
            "Caption EN": "hello",
        }

        normalized = _normalize_bitable_fields(fields)

        self.assertEqual(normalized["重推次数"], 0)
        self.assertEqual(normalized["重生版本号"], 2.5)
        self.assertEqual(normalized["Caption EN"], "hello")

    def test_normalize_bitable_fields_converts_url_style_text_fields(self):
        fields = {
            "AI生成图链接": "https://example.com/image.png",
            "主图URL": "",
            "账号链接": "https://www.instagram.com/example/",
            "样例帖子1链接": "https://www.instagram.com/p/example/\nhttps://www.instagram.com/p/extra/",
            "Caption EN": "https://example.com should stay plain here",
        }

        normalized = _normalize_bitable_fields(fields)

        self.assertEqual(
            normalized["AI生成图链接"],
            {"link": "https://example.com/image.png", "text": "https://example.com/image.png"},
        )
        self.assertNotIn("主图URL", normalized)
        self.assertEqual(
            normalized["账号链接"],
            {"link": "https://www.instagram.com/example/", "text": "https://www.instagram.com/example/"},
        )
        self.assertEqual(
            normalized["样例帖子1链接"],
            {
                "link": "https://www.instagram.com/p/example/",
                "text": "https://www.instagram.com/p/example/\nhttps://www.instagram.com/p/extra/",
            },
        )
        self.assertEqual(normalized["Caption EN"], "https://example.com should stay plain here")
        self.assertEqual(fields["AI生成图链接"], "https://example.com/image.png")
