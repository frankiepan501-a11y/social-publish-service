import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.generation import (
    build_update_fields,
    fallback_generation,
    generation_candidate_reason,
    parse_ai_json,
    required_generation_missing,
    validate_generation_payload,
)
from app.main import app


def fields(**overrides):
    base = {
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
    base.update(overrides)
    return base


class GenerationRulesTest(unittest.TestCase):
    def test_required_generation_missing(self):
        missing = required_generation_missing(fields(**{"品牌": "", "平台": []}))
        self.assertIn("品牌", missing)
        self.assertIn("平台", missing)

    def test_manual_locks_skip_overwrite(self):
        source = fields(**{"文案人工锁定": True, "图片Prompt人工锁定": True})
        payload = fallback_generation(source)
        updates = build_update_fields(source, payload, run_id="genv1-test", source="auto")
        self.assertNotIn("Caption EN", updates)
        self.assertNotIn("Hashtag EN", updates)
        self.assertNotIn("AI图片Prompt", updates)
        self.assertEqual(updates["AI生成状态"], "人工锁定")
        self.assertEqual(updates["状态"], "待审核")

    def test_generation_candidate_reason(self):
        ok, reason = generation_candidate_reason(fields())
        self.assertTrue(ok)
        self.assertEqual(reason, "candidate")
        ok, reason = generation_candidate_reason(fields(**{"状态": "待发布"}))
        self.assertFalse(ok)
        self.assertEqual(reason, "status=待发布")

    def test_validate_generation_payload_accepts_template(self):
        payload = fallback_generation(fields())
        self.assertEqual(validate_generation_payload(payload), [])

    def test_validate_generation_payload_blocks_missing_fields(self):
        payload = replace(fallback_generation(fields()), caption_en="", hashtags_en="GamingSetup", risk_level="maybe")
        issues = validate_generation_payload(payload)
        self.assertIn("CAPTION_EN_EMPTY", issues)
        self.assertIn("HASHTAG_FORMAT_INVALID", issues)
        self.assertIn("RISK_LEVEL_INVALID", issues)

    def test_validate_generation_payload_blocks_caption_banned_terms(self):
        payload = replace(fallback_generation(fields()), caption_en="Official Mario setup gear for your desk.")
        issues = validate_generation_payload(payload)
        self.assertIn("CAPTION_BLOCK_TERM:mario", issues)

    def test_validate_generation_payload_blocks_image_prompt_logo_instruction(self):
        payload = replace(
            fallback_generation(fields()),
            image_prompt="Add a logo overlay and text overlay on top of the product photo.",
        )
        issues = validate_generation_payload(payload)
        self.assertIn("IMAGE_PROMPT_SAFETY_GUARD_MISSING", issues)
        self.assertIn("IMAGE_PROMPT_FORBIDDEN_RENDER_INSTRUCTION", issues)

    def test_parse_ai_json_hardens_missing_image_prompt_guards(self):
        payload = parse_ai_json(
            """
            {
              "brief": "- Brief",
              "hook_hypothesis": "Test a setup hook.",
              "caption_en": "Make your setup cleaner with a compact charging dock.",
              "hashtags_en": "#GamingSetup #DeskSetup #SwitchAccessories",
              "caption_cn_note": "围绕场景和保存动机写。",
              "image_prompt": "Bright ecommerce product photo on a clean gaming desk",
              "publish_checklist": "确认素材\\n确认链接",
              "risk_checklist": "不使用未授权素材",
              "risk_level": "normal"
            }
            """
        )
        self.assertIn("no text", payload.image_prompt.lower())
        self.assertIn("no logo", payload.image_prompt.lower())
        self.assertEqual(validate_generation_payload(payload), [])

    def test_parse_ai_json_normalizes_plain_hashtags(self):
        payload = parse_ai_json(
            """
            {
              "brief": "- Brief",
              "hook_hypothesis": "Test a setup hook.",
              "caption_en": "Make your setup cleaner with a compact charging dock.",
              "hashtags_en": "GamingSetup DeskSetup SwitchAccessories",
              "caption_cn_note": "围绕场景和保存动机写。",
              "image_prompt": "Bright ecommerce product photo, no text, no logo, no watermark",
              "publish_checklist": "确认素材\\n确认链接",
              "risk_checklist": "不使用未授权素材",
              "risk_level": "normal"
            }
            """
        )
        self.assertEqual(payload.hashtags_en, "#GamingSetup #DeskSetup #SwitchAccessories")
        self.assertEqual(validate_generation_payload(payload), [])

    def test_generate_brief_dry_run(self):
        client = TestClient(app)
        resp = client.post(
            "/generate/brief",
            json={"record_id": "inline-test", "record": {"fields": fields()}, "write_back": False},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "dry-run-generated")
        self.assertIn("Caption EN", body["fields"])
        self.assertIn("AI图片Prompt", body["fields"])

    def test_image_task_create_dry_run_builds_codex_worker_payload(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/create",
            json={
                "record_id": "rec_image_source",
                "record": {
                    "fields": fields(
                        **{
                            "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                            "图片生成模式": "Codex Image",
                            "场景模板": "FB/INS广告图",
                        }
                    )
                },
                "write_task": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "dry-run-task-built")
        self.assertEqual(body["task_fields"]["工作流选择"], "Codex Image")
        self.assertEqual(body["task_fields"]["状态"], "待处理")
        self.assertIn("rec_image_source", body["task_fields"]["自定义提示词"])

    def test_image_task_write_requires_explicit_gate(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/create",
            json={
                "record_id": "rec_image_source",
                "record": {
                    "fields": fields(
                        **{
                            "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                            "图片生成模式": "Codex Image",
                        }
                    )
                },
                "write_task": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["blocking"][0]["code"], "IMAGE_TASK_WRITE_DISABLED")

    def test_image_result_ingest_dry_run_maps_worker_file_token(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/ingest",
            json={
                "record_id": "rec_image_source",
                "record": {"fields": fields(**{"图片任务record_id": "rec_worker"})},
                "image_task_record_id": "rec_worker",
                "image_task_record": {
                    "fields": {
                        "状态": "处理成功",
                        "生成图片位置": "https://u1wpma3xuhr.feishu.cn/drive/folder/folder_token\nimage.png",
                        "生成图片file_token": "filetoken_123",
                    }
                },
                "write_back": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["fields"]["图片生成状态"], "已生成-待转URL")
        self.assertEqual(body["fields"]["生成图片file_token"], "filetoken_123")

    def test_image_task_scan_selects_only_pending_codex_image_candidates(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/scan",
            json={
                "records": [
                    {
                        "record_id": "rec_img_ok",
                        "fields": fields(
                            **{
                                "状态": "待审核",
                                "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                                "图片生成模式": "Codex Image",
                                "图片生成状态": "待生成",
                            }
                        ),
                    },
                    {
                        "record_id": "rec_img_skip",
                        "fields": fields(
                            **{
                                "状态": "待审核",
                                "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                                "图片生成模式": "人工上传",
                            }
                        ),
                    },
                ],
                "write_task": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_img_ok")
        self.assertEqual(body["skipped_sample"][0]["record_id"], "rec_img_skip")

    def test_generate_writeback_requires_explicit_gate(self):
        client = TestClient(app)
        resp = client.post(
            "/generate/brief",
            json={"record_id": "inline-test", "record": {"fields": fields()}, "write_back": True},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["blocking"][0]["code"], "GENERATION_WRITEBACK_DISABLED")

    def test_generate_writeback_gate_blocks_before_input_error_writeback(self):
        client = TestClient(app)
        with patch.object(main_module, "_mark_generation_error", new_callable=AsyncMock) as mark_error:
            resp = client.post(
                "/generate/brief",
                json={
                    "record_id": "inline-test",
                    "record": {"fields": fields(**{"品牌": "", "平台": []})},
                    "write_back": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["blocking"][0]["code"], "GENERATION_WRITEBACK_DISABLED")
        mark_error.assert_not_awaited()

    def test_generate_dry_run_missing_input_does_not_write_error(self):
        client = TestClient(app)
        with patch.object(main_module, "_mark_generation_error", new_callable=AsyncMock) as mark_error:
            resp = client.post(
                "/generate/brief",
                json={
                    "record_id": "inline-test",
                    "record": {"fields": fields(**{"品牌": "", "平台": []})},
                    "write_back": False,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "blocked")
        self.assertIn("品牌", body["missing"])
        mark_error.assert_not_awaited()

    def test_generate_brief_blocks_invalid_ai_output(self):
        client = TestClient(app)
        bad_payload = replace(fallback_generation(fields()), caption_en="Official Mario setup gear for your desk.")
        with patch.object(main_module, "generate_payload", new_callable=AsyncMock) as generate_payload, patch.object(
            main_module, "_mark_generation_error", new_callable=AsyncMock
        ) as mark_error:
            generate_payload.return_value = (bad_payload, "test-ai")
            resp = client.post(
                "/generate/brief",
                json={"record_id": "inline-test", "record": {"fields": fields()}, "write_back": False},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "generation-failed")
        self.assertEqual(body["reason"], "GENERATION_OUTPUT_INVALID")
        self.assertIn("CAPTION_BLOCK_TERM:mario", body["issues"])
        self.assertNotIn("fields", body)
        mark_error.assert_not_awaited()

    def test_generate_scan_inline_records(self):
        client = TestClient(app)
        resp = client.post(
            "/generate/scan",
            json={
                "records": [
                    {"record_id": "rec_ok", "fields": fields()},
                    {"record_id": "rec_skip", "fields": fields(**{"状态": "待发布"})},
                ],
                "write_back": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["generated"], 1)
        self.assertTrue(body["scan_run_id"].startswith("gscanv1-"))
        self.assertEqual(body["results"][0]["record_id"], "rec_ok")
        self.assertEqual(body["skipped_sample"][0]["record_id"], "rec_skip")

    def test_generate_scan_failed_result_includes_record_id(self):
        client = TestClient(app)
        resp = client.post(
            "/generate/scan",
            json={
                "records": [{"record_id": "rec_bad", "fields": fields()}],
                "write_back": True,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_bad")
        self.assertEqual(body["results"][0]["blocking"][0]["code"], "GENERATION_WRITEBACK_DISABLED")

    def test_generate_scan_logs_empty_scan_summary(self):
        client = TestClient(app)
        with patch.object(main_module, "_write_log", new_callable=AsyncMock) as write_log:
            resp = client.post(
                "/generate/scan",
                json={
                    "records": [{"record_id": "rec_skip", "fields": fields(**{"状态": "待发布"})}],
                    "write_back": False,
                    "limit": 10,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["generated"], 0)
        write_log.assert_awaited_once()
        kwargs = write_log.await_args.kwargs
        self.assertEqual(kwargs["node"], "generate/scan")
        self.assertEqual(kwargs["record_id"], "__scan__")
        self.assertEqual(kwargs["status"], "success")
        self.assertIn('"selected": 0', kwargs["output_summary"])
        self.assertIn("POST /generate/scan", kwargs["replay_command"])

    def test_write_log_respects_dry_run_log_gate(self):
        settings = Settings(feishu_app_id="app", feishu_app_secret="secret", dry_run_write_logs=False)
        feishu = AsyncMock()
        with patch.object(main_module, "_feishu", return_value=feishu):
            asyncio.run(
                main_module._write_log(
                    settings,
                    run_id="genv1-test",
                    record_id="rec_test",
                    node="generate/brief",
                    status="dry-run",
                    input_hash="hash",
                    output_summary="summary",
                    decision_reason="generated",
                    mode="dry-run",
                )
            )
        feishu.append_run_log.assert_not_awaited()

    def test_write_log_keeps_commit_audit(self):
        settings = Settings(feishu_app_id="app", feishu_app_secret="secret", dry_run_write_logs=False)
        feishu = AsyncMock()
        with patch.object(main_module, "_feishu", return_value=feishu):
            asyncio.run(
                main_module._write_log(
                    settings,
                    run_id="spv1-test",
                    record_id="rec_test",
                    node="publish/commit",
                    status="blocked",
                    input_hash="hash",
                    output_summary="summary",
                    decision_reason="COMMIT_DISABLED",
                    mode="commit",
                )
            )
        feishu.append_run_log.assert_awaited_once()

    def test_generation_replay_routes_to_generate_dry_run(self):
        client = TestClient(app)
        resp = client.post(
            "/replay",
            json={
                "run_id": "genv1-previous",
                "record_id": "inline-replay",
                "record": {"fields": fields()},
                "mode": "dry-run",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "dry-run-generated")
        self.assertEqual(body["fields"]["生成来源"], "replay")
        self.assertIn("Caption EN", body["fields"])
        self.assertIn("AI图片Prompt", body["fields"])

    def test_generation_replay_rejects_commit_mode(self):
        client = TestClient(app)
        resp = client.post(
            "/replay",
            json={
                "run_id": "genv1-previous",
                "record_id": "inline-replay",
                "record": {"fields": fields()},
                "mode": "commit",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("generation replay only supports dry-run", resp.text)

    def test_replay_rejects_unknown_run_id_prefix(self):
        client = TestClient(app)
        resp = client.post(
            "/replay",
            json={
                "run_id": "unknown-previous",
                "record_id": "inline-replay",
                "record": {"fields": fields()},
                "mode": "dry-run",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown replay run_id prefix", resp.text)


if __name__ == "__main__":
    unittest.main()
