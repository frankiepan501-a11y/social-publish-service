import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
import app.main as main_module
from app.main import _copy_product_references_to_image_base, _extract_image_result_fields
from app.models import ImageTaskRequest


class FakeContentClient:
    def __init__(self):
        self.downloaded_urls = []
        self.downloaded_tokens = []
        self.updates = []

    async def download_media_url(self, url):
        self.downloaded_urls.append(url)
        return b"image-by-url", "image/png"

    async def download_media(self, file_token):
        self.downloaded_tokens.append(file_token)
        return b"image-by-token", "image/png"

    async def update_record(self, table_id, record_id, fields):
        self.updates.append({"table_id": table_id, "record_id": record_id, "fields": fields})
        return {"record": {"record_id": record_id, "fields": fields}}


class FakeImageClient:
    def __init__(self):
        self.uploads = []
        self.created = []

    async def upload_bitable_media(self, *, file_name, content, content_type):
        self.uploads.append(
            {"file_name": file_name, "content": content, "content_type": content_type}
        )
        return "copied-token"

    async def create_record(self, table_id, fields):
        self.created.append({"table_id": table_id, "fields": fields})
        return {"record": {"record_id": "rec_worker"}}


class ImageTaskReferenceCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_download_url_is_used_before_file_token(self):
        content = FakeContentClient()
        image = FakeImageClient()
        copied = await _copy_product_references_to_image_base(
            {
                "产品参考图包": [
                    {
                        "file_token": "source-token",
                        "name": "source.png",
                        "url": "https://open.feishu.cn/open-apis/drive/v1/medias/source-token/download?extra=bitablePerm",
                    }
                ]
            },
            content_client=content,
            image_client=image,
        )

        self.assertEqual(content.downloaded_urls, [
            "https://open.feishu.cn/open-apis/drive/v1/medias/source-token/download?extra=bitablePerm"
        ])
        self.assertEqual(content.downloaded_tokens, [])
        self.assertEqual(image.uploads[0]["content"], b"image-by-url")
        self.assertEqual(copied["产品参考图包"][0]["file_token"], "copied-token")

    async def test_image_task_commit_writes_existing_content_fields_only(self):
        content = FakeContentClient()
        image = FakeImageClient()
        settings = Settings(
            feishu_app_id="app",
            feishu_app_secret="secret",
            image_task_write_enabled=True,
            generation_ai_provider="template",
        )
        req = ImageTaskRequest(
            record_id="rec_content",
            record={
                "fields": {
                    "内容标题": "Powkong setup angle",
                    "产品名": "Powkong Switch 2 Dock",
                    "品牌": "Powkong",
                    "平台": ["Instagram"],
                    "发布位置": ["IG Feed"],
                    "素材类型": "single_image",
                    "状态": "选题中",
                    "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                    "产品参考图": [
                        {
                            "file_token": "source-token",
                            "name": "source.png",
                            "url": "https://open.feishu.cn/open-apis/drive/v1/medias/source-token/download?extra=bitablePerm",
                        }
                    ],
                }
            },
            write_task=True,
        )

        with (
            patch.object(main_module, "_feishu", return_value=content),
            patch.object(main_module, "_feishu_image", return_value=image),
            patch.object(
                main_module,
                "_enrich_content_fields_with_product_context",
                AsyncMock(side_effect=lambda fields, _settings: fields),
            ),
            patch.object(main_module, "_write_log", AsyncMock()),
        ):
            result = await main_module._execute_image_task(req, settings)

        self.assertTrue(result["ok"])
        self.assertEqual(result["image_task_record_id"], "rec_worker")
        self.assertEqual(len(content.updates), 1)
        update_fields = content.updates[0]["fields"]
        self.assertNotIn("图片生成模式", update_fields)
        self.assertEqual(update_fields["图片生成状态"], "已提交")
        self.assertEqual(update_fields["图片任务record_id"], "rec_worker")

    async def test_image_task_pending_status_repairs_known_mojibake(self):
        _updates, blocking = _extract_image_result_fields(
            "rec_worker",
            {
                "状态": "덤뇹잿",
            },
        )

        self.assertEqual(blocking, ["IMAGE_TASK_NOT_DONE:待处理", "IMAGE_RESULT_MISSING"])
