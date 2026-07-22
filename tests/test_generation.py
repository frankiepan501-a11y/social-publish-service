import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.generation import (
    brand_product_label,
    build_prompt,
    build_update_fields,
    fallback_generation,
    generation_candidate_reason,
    normalize_hashtags,
    parse_ai_json,
    required_generation_missing,
    validate_generation_payload,
)
from app.main import app
from app.rules import AccountConfig


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
        "产品参考图": [{"file_token": "ref_file_token", "name": "reference.png"}],
    }
    base.update(overrides)
    return base


def publish_ready_fields(**overrides):
    base = fields(
        **{
            "状态": "待发布",
            "发布模式": "auto",
            "计划发布账号": "Powkong IG",
            "计划发布时间": "2026-07-01 10:00:00",
            "审批通过": True,
            "最终素材确认": True,
            "Caption EN": "Keep your desk clean with a compact charging setup.",
            "主图URL": "https://cdn.example.com/powkong-desk.png",
        }
    )
    base.update(overrides)
    return base


def publish_account():
    return AccountConfig(
        account_name="Powkong IG",
        brand="Powkong",
        platform="Instagram",
        publish_slots=["IG Feed"],
        ig_user_id="ig-user-1",
        daily_limit=1,
        weekly_limit=3,
        min_interval_hours=36,
        enabled=True,
        default_mode="auto",
    )


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
        ok, reason = generation_candidate_reason(fields(**{"产品参考图": []}))
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_product_reference_image")
        ok, reason = generation_candidate_reason(fields(**{"品牌": "FUNLAB"}))
        self.assertFalse(ok)
        self.assertEqual(reason, "FUNLAB_IP_COMPLIANCE_MISSING")

    def test_validate_generation_payload_accepts_template(self):
        payload = fallback_generation(fields())
        self.assertEqual(validate_generation_payload(payload, fields()), [])

    def test_build_prompt_includes_brand_vi_and_reference_guard(self):
        system, user = build_prompt(fields())
        self.assertIn("valid JSON", system)
        self.assertIn("Powkong Orange #FF9D00", user)
        self.assertIn("Use the attached product reference image", user)
        self.assertIn("Preserve the exact product shape", user)

    def test_build_prompt_includes_seo_geo_and_reference_object(self):
        _system, user = build_prompt(
            fields(
                **{
                    "参考对象": "8BitDo-style living room post",
                    "参考理由": "借鉴电视游戏场景",
                    "借鉴元素": "手持视角、电视背景、低亮度氛围",
                    "禁止复制元素": "竞品 logo、产品外观、屏幕 UI",
                    "SEO主关键词": "switch controller for couch gaming",
                    "GEO目标问题": "What controller should I use for couch gaming?",
                    "搜索意图": "信息型",
                    "Hashtag词组池": "#Powkong #GamingController #DeskSetup #CouchGaming #SetupInspo",
                }
            )
        )
        self.assertIn("switch controller for couch gaming", user)
        self.assertIn("What controller should I use for couch gaming?", user)
        self.assertIn("8BitDo-style living room post", user)
        self.assertIn("Do not copy competitor product appearance", user)

    def test_build_prompt_hides_ip_inspired_internal_model_names(self):
        _system, user = build_prompt(
            fields(
                **{
                    "品牌": "FUNLAB",
                    "产品名": "FUNLAB YS11 Pro Controller - Abstract Wave",
                    "产品库型号英文名": "Zonai",
                    "产品库适配IP/IP联想": "Kakariko-inspired pattern",
                    "产品库IP合规状态": "合规-无IP",
                }
            )
        )
        self.assertIn("FUNLAB YS11 Pro Controller - Abstract Wave", user)
        self.assertNotIn("Zonai", user)
        self.assertNotIn("Kakariko", user)

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

    def test_validate_generation_payload_blocks_ip_inspired_public_terms(self):
        payload = replace(
            fallback_generation(fields()),
            caption_en="The Zonai pattern stays hidden until you power on.",
            hashtags_en="#HiddenGlow #Zonai #GamingSetup",
            image_prompt=(
                "Use the attached product reference image and preserve the exact product. "
                "Low-light desk scene with a Zonai-inspired pattern, no text, no new logo overlay, no watermark."
            ),
        )
        issues = validate_generation_payload(payload, fields())
        self.assertIn("CAPTION_BLOCK_TERM:zonai", issues)
        self.assertIn("HASHTAG_BLOCK_TERM:zonai", issues)
        self.assertIn("IMAGE_PROMPT_BLOCK_TERM:zonai", issues)

    def test_validate_generation_payload_blocks_image_prompt_logo_instruction(self):
        payload = replace(
            fallback_generation(fields()),
            image_prompt="Add a logo overlay and text overlay on top of the product photo.",
        )
        issues = validate_generation_payload(payload)
        self.assertIn("IMAGE_PROMPT_SAFETY_GUARD_MISSING", issues)
        self.assertIn("IMAGE_PROMPT_FORBIDDEN_RENDER_INSTRUCTION", issues)

    def test_validate_generation_payload_blocks_positive_product_change(self):
        payload = replace(
            fallback_generation(fields()),
            image_prompt=(
                "Use the attached product reference image and preserve the product, "
                "but redesign the product as a blue premium controller, no text, no new logo overlay, no watermark."
            ),
        )
        issues = validate_generation_payload(payload, fields())
        self.assertIn("IMAGE_PROMPT_FORBIDDEN_PRODUCT_CHANGE", issues)

    def test_brand_product_label_does_not_duplicate_brand_prefix(self):
        self.assertEqual(
            brand_product_label("FUNLAB", "FUNLAB YS11 Pro Controller - Zonai"),
            "FUNLAB YS11 Pro Controller - Zonai",
        )
        self.assertEqual(brand_product_label("Powkong", "Dock Gen 2"), "Powkong Dock Gen 2")

    def test_funlab_generation_requires_ip_compliance(self):
        payload = fallback_generation(fields(**{"品牌": "FUNLAB"}))
        issues = validate_generation_payload(payload, fields(**{"品牌": "FUNLAB"}))
        self.assertIn("FUNLAB_IP_COMPLIANCE_MISSING", issues)
        ok_fields = fields(**{"品牌": "FUNLAB", "产品库IP合规状态": "合规-无IP"})
        self.assertEqual(validate_generation_payload(payload, ok_fields), [])
        blocked_fields = fields(**{"品牌": "FUNLAB", "产品库IP合规状态": "风险-限非Funlab"})
        self.assertIn("FUNLAB_IP_COMPLIANCE_BLOCKED:风险-限非Funlab", validate_generation_payload(payload, blocked_fields))

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
        self.assertIn("no new logo overlay", payload.image_prompt.lower())
        self.assertEqual(validate_generation_payload(payload), [])

    def test_build_update_fields_writes_seo_geo_note(self):
        source = fields(
            **{
                "SEO主关键词": "switch controller setup",
                "GEO目标问题": "How do I choose a Switch controller?",
                "Hashtag词组池": "#Powkong #GamingController #DeskSetup #SetupInspo #GameRoom",
            }
        )
        payload = fallback_generation(source)
        issues = validate_generation_payload(payload, source)
        self.assertEqual(issues, [])
        updates = build_update_fields(source, payload, run_id="genv1-test", source="manual")
        self.assertIn("SEO/GEO生成说明", updates)
        self.assertIn("SEO/GEO", updates["SEO/GEO生成说明"])

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

    def test_normalize_hashtags_cleans_punctuation(self):
        self.assertEqual(
            normalize_hashtags("#HiddenGlow, #GamingSetup, ControllerDesign"),
            "#HiddenGlow #GamingSetup #ControllerDesign",
        )

    def test_generate_payload_falls_back_when_ai_json_is_invalid(self):
        settings = Settings(
            generation_ai_provider="deepseek",
            generation_ai_api_key="test-key",
            generation_ai_model="deepseek-chat",
        )
        with patch("app.generation.OpenAICompatibleClient") as client_cls:
            client_cls.return_value.chat_json = AsyncMock(return_value="I cannot provide JSON for this request.")
            payload, provider = asyncio.run(main_module.generate_payload(fields(), settings))
        self.assertEqual(provider, "deepseek-chat+template-fallback-json")
        self.assertIn("Caption EN", build_update_fields(fields(), payload, run_id="genv1-test", source="manual"))
        self.assertEqual(validate_generation_payload(payload, fields()), [])

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
                            "设计参考图": [{"file_token": "design_ref_token", "name": "design.png"}],
                            "细节参考图": [{"file_token": "detail_ref_token", "name": "buttons.png"}],
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
        self.assertEqual(body["task_fields"]["产品原图"][0]["file_token"], "ref_file_token")
        self.assertEqual(body["task_fields"]["产品参考图包"][0]["file_token"], "ref_file_token")
        self.assertEqual(body["task_fields"]["设计参考图"][0]["file_token"], "design_ref_token")
        self.assertEqual(body["task_fields"]["细节参考图"][0]["file_token"], "detail_ref_token")
        self.assertEqual(body["task_fields"]["参考图使用策略"], "产品保真优先")
        self.assertIn("设计参考图 is for scene", body["task_fields"]["自定义提示词"])
        self.assertIn("细节参考图 overrides", body["task_fields"]["自定义提示词"])
        self.assertIn("Reference budget", body["task_fields"]["自定义提示词"])
        self.assertIn("preserve the shown small-control count", body["task_fields"]["自定义提示词"])
        self.assertIn("exact source of truth", body["task_fields"]["自定义提示词"])

    def test_image_task_create_limits_reference_budget(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/create",
            json={
                "record_id": "rec_image_source",
                "record": {
                    "fields": fields(
                        **{
                            "AI图片Prompt": "Borrow the gaming TV scene only; preserve the FUNLAB controller exactly.",
                            "图片生成模式": "Codex Image",
                            "产品参考图": [
                                {"file_token": "port_token", "name": "04-copy.png"},
                                {"file_token": "main_token", "name": "image-02-copy.png"},
                                {"file_token": "angle_token", "name": "01-copy.png"},
                            ],
                            "设计参考图": [
                                {"file_token": "design_one", "name": "competitor-scene.png"},
                                {"file_token": "design_two", "name": "alternate-scene.png"},
                            ],
                            "细节参考图": [
                                {"file_token": "surface_detail", "name": "surface.png"},
                                {
                                    "file_token": "button_detail",
                                    "name": "FUNLAB_YS11波纹款_AI按键参考_中间功能键特写_图标清晰_v01.png",
                                },
                            ],
                        }
                    )
                },
                "write_task": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["task_fields"]["产品参考图包"]), 1)
        self.assertEqual(body["task_fields"]["产品参考图包"][0]["file_token"], "main_token")
        self.assertEqual(body["task_fields"]["产品原图"][0]["file_token"], "main_token")
        self.assertEqual(len(body["task_fields"]["设计参考图"]), 1)
        self.assertEqual(body["task_fields"]["设计参考图"][0]["file_token"], "design_one")
        self.assertEqual(len(body["task_fields"]["细节参考图"]), 1)
        self.assertEqual(body["task_fields"]["细节参考图"][0]["file_token"], "button_detail")
        self.assertIn("do not blend unselected product angles", body["task_fields"]["自定义提示词"])

    def test_image_task_create_blocks_missing_reference_image(self):
        client = TestClient(app)
        source = fields(
            **{
                "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                "图片生成模式": "Codex Image",
                "产品参考图": [],
            }
        )
        resp = client.post(
            "/image-task/create",
            json={"record_id": "rec_image_source", "record": {"fields": source}, "write_task": False},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("产品参考图/产品原图", body["missing"])

    def test_image_task_create_blocks_funlab_ip_risk(self):
        client = TestClient(app)
        source = fields(
            **{
                "品牌": "FUNLAB",
                "产品库IP合规状态": "禁售-高风险",
                "AI图片Prompt": "Dark product-first setup, no text, no logo, no watermark.",
                "图片生成模式": "Codex Image",
            }
        )
        resp = client.post(
            "/image-task/create",
            json={"record_id": "rec_image_source", "record": {"fields": source}, "write_task": False},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("FUNLAB_IP_COMPLIANCE_BLOCKED:禁售-高风险", body["missing"])

    def test_image_task_create_blocks_approved_publish_ready_content(self):
        client = TestClient(app)
        source = fields(
            **{
                "状态": "待发布",
                "审批通过": True,
                "最终素材确认": True,
                "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                "图片生成模式": "Codex Image",
            }
        )
        resp = client.post(
            "/image-task/create",
            json={"record_id": "rec_publish_ready", "record": {"fields": source}, "write_task": False, "force": True},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("approved/publish-ready content requires approval regeneration action", body["missing"])

    def test_product_context_merge_adds_reference_image_and_ip_fields(self):
        product_record = {
            "record_id": "rec_product_1",
            "fields": {
                "图片": [{"file_token": "product_ref_token", "name": "product.png"}],
                "产品简述": "Hidden Glow controller with a low-light reveal.",
                "系列英文名": "Firefly",
                "型号英文名": "Firefly Controller",
                "IP合规状态": "合规-无IP",
                "IP合规备注": "abstract pattern only",
                "适配IP/IP联想": "abstract glow pattern",
                "品牌型号": "FF01A-01",
            },
        }
        merged = main_module._merge_product_context_fields(fields(**{"品牌": "FUNLAB", "产品参考图": []}), product_record)
        self.assertEqual(merged["产品库记录ID"], "rec_product_1")
        self.assertEqual(merged["产品参考图包"][0]["file_token"], "product_ref_token")
        self.assertEqual(merged["产品参考图"][0]["file_token"], "product_ref_token")
        self.assertEqual(merged["产品库IP合规状态"], "合规-无IP")
        self.assertEqual(merged["IP合规状态"], "合规-无IP")
        self.assertEqual(merged["产品库产品简述"], "Hidden Glow controller with a low-light reveal.")

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

    def test_image_result_ingest_blocks_result_equal_to_source_reference(self):
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
                        "产品参考图包": [{"file_token": "source_token"}],
                        "生成图片file_token": "source_token",
                    }
                },
                "write_back": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("IMAGE_RESULT_EQUALS_SOURCE_REFERENCE", [item["code"] for item in body["blocking"]])

    def test_image_result_ingest_omits_non_url_location_from_image_link(self):
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
                        "生成图片位置": "canary fallback: reused product reference image",
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
        self.assertNotIn("AI生成图链接", body["fields"])

    def test_image_result_ingest_writeback_retries_without_optional_run_id(self):
        class FakeClient:
            def __init__(self):
                self.updates = []

            async def update_record(self, table_id, record_id, update_fields):
                self.updates.append(update_fields)
                if "运行/回放ID" in update_fields:
                    raise main_module.FeishuError("Feishu API error: HTTP 400, code=1254045, msg=field 运行/回放ID not found")
                return {"record": {"record_id": record_id}}

            async def append_run_log(self, *args, **kwargs):
                return None

        fake = FakeClient()
        settings = Settings(
            feishu_app_id="app",
            feishu_app_secret="secret",
            image_result_writeback_enabled=True,
            dry_run_write_logs=False,
        )
        req = main_module.ImageResultIngestRequest(
            record_id="rec_image_source",
            record={"fields": fields(**{"图片任务record_id": "rec_worker"})},
            image_task_record_id="rec_worker",
            image_task_record={
                "fields": {
                    "状态": "处理成功",
                    "生成图片file_token": "filetoken_123",
                }
            },
            write_back=True,
        )
        with patch.object(main_module, "_feishu", return_value=fake):
            result = asyncio.run(main_module._execute_image_result_ingest(req, settings=settings))
        self.assertTrue(result["ok"])
        self.assertEqual(len(fake.updates), 2)
        self.assertIn("运行/回放ID", fake.updates[0])
        self.assertNotIn("运行/回放ID", fake.updates[1])

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

    def test_image_task_scan_skips_terminal_failed_image_status(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/scan",
            json={
                "records": [
                    {
                        "record_id": "rec_img_failed",
                        "fields": fields(
                            **{
                                "状态": "待审核",
                                "AI图片Prompt": "Product-first bright desk setup, no text, no logo, no watermark.",
                                "图片生成模式": "Codex Image",
                                "图片生成状态": "失败",
                            }
                        ),
                    }
                ],
                "write_task": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["created"], 0)
        self.assertEqual(body["skipped_sample"][0]["reason"], "image_status=失败")

    def test_image_result_ingest_scan_selects_pending_task_records(self):
        client = TestClient(app)
        with patch.object(main_module, "_execute_image_result_ingest", new_callable=AsyncMock) as ingest:
            ingest.return_value = {"ok": True, "status": "image-result-ready", "run_id": "imgresultv1-test"}
            resp = client.post(
                "/image-task/ingest-scan",
                json={
                    "records": [
                        {
                            "record_id": "rec_pending",
                            "fields": fields(
                                **{
                                    "图片任务record_id": "rec_worker",
                                    "图片生成状态": "已提交",
                                }
                            ),
                        },
                        {
                            "record_id": "rec_done",
                            "fields": fields(
                                **{
                                    "图片任务record_id": "rec_worker_done",
                                    "图片生成状态": "已转发布URL",
                                    "AI生成图链接": "https://cdn.example.com/image.png",
                                    "生成图片file_token": "filetoken_done",
                                }
                            ),
                        },
                    ],
                    "write_back": False,
                    "limit": 10,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["ingested"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_pending")
        self.assertEqual(body["skipped_sample"][0]["record_id"], "rec_done")
        self.assertEqual(body["skipped_sample"][0]["reason"], "image_result_already_public")
        ingest.assert_awaited_once()
        self.assertFalse(ingest.await_args.args[0].write_back)

    def test_image_result_ingest_scan_skips_terminal_failed_content_status(self):
        client = TestClient(app)
        with patch.object(main_module, "_execute_image_result_ingest", new_callable=AsyncMock) as ingest:
            resp = client.post(
                "/image-task/ingest-scan",
                json={
                    "records": [
                        {
                            "record_id": "rec_failed",
                            "fields": fields(
                                **{
                                    "图片任务record_id": "rec_worker_failed",
                                    "图片生成状态": "失败",
                                    "图片生成错误": "manual queue cleanup",
                                }
                            ),
                        }
                    ],
                    "write_back": False,
                    "limit": 10,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["ingested"], 0)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["skipped_sample"][0]["reason"], "image_result_failed")
        ingest.assert_not_awaited()

    def test_image_result_ingest_scan_inline_worker_record_maps_file_token(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/ingest-scan",
            json={
                "records": [
                    {
                        "record_id": "rec_pending",
                        "fields": fields(
                            **{
                                "图片任务record_id": "rec_worker",
                                "图片生成状态": "已提交",
                            }
                        ),
                        "image_task_record_id": "rec_worker",
                        "image_task_record": {
                            "fields": {
                                "状态": "处理成功",
                                "生成图片位置": "https://u1wpma3xuhr.feishu.cn/drive/folder/folder_token/image.png",
                                "生成图片file_token": "filetoken_456",
                            }
                        },
                    }
                ],
                "write_back": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        fields_out = body["results"][0]["fields"]
        self.assertEqual(fields_out["图片生成状态"], "已生成-待转URL")
        self.assertEqual(fields_out["生成图片file_token"], "filetoken_456")

    def test_image_result_ingest_scan_inline_worker_failure_maps_to_content_failure(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/ingest-scan",
            json={
                "records": [
                    {
                        "record_id": "rec_pending",
                        "fields": fields(
                            **{
                                "图片任务record_id": "rec_worker",
                                "图片生成状态": "已提交",
                            }
                        ),
                        "image_task_record_id": "rec_worker",
                        "image_task_record": {
                            "fields": {
                                "状态": "失败",
                                "错误信息": "worker test task archived",
                            }
                        },
                    }
                ],
                "write_back": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["ingested"], 1)
        self.assertEqual(body["failed"], 0)
        fields_out = body["results"][0]["fields"]
        self.assertEqual(fields_out["图片生成状态"], "失败")
        self.assertEqual(fields_out["图片生成错误"], "worker test task archived")

    def test_image_result_ingest_scan_treats_worker_pending_as_noop(self):
        client = TestClient(app)
        resp = client.post(
            "/image-task/ingest-scan",
            json={
                "records": [
                    {
                        "record_id": "rec_pending",
                        "fields": fields(
                            **{
                                "图片任务record_id": "rec_worker",
                                "图片生成状态": "已提交",
                            }
                        ),
                        "image_task_record_id": "rec_worker",
                        "image_task_record": {"fields": {"状态": "待处理"}},
                    }
                ],
                "write_back": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["ingested"], 0)
        self.assertEqual(body["pending"], 1)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["results"][0]["record_id"], "rec_pending")
        self.assertEqual(body["results"][0]["blocking"][0]["code"], "IMAGE_TASK_NOT_DONE:待处理")

    def test_image_result_ingest_scan_failed_result_includes_record_id(self):
        client = TestClient(app)
        with patch.object(main_module, "_execute_image_result_ingest", new_callable=AsyncMock) as ingest:
            ingest.return_value = {
                "ok": False,
                "status": "blocked",
                "run_id": "imgresultv1-test",
                "blocking": [{"code": "IMAGE_RESULT_WRITEBACK_DISABLED"}],
            }
            resp = client.post(
                "/image-task/ingest-scan",
                json={
                    "records": [
                        {
                            "record_id": "rec_pending",
                            "fields": fields(
                                **{
                                    "图片任务record_id": "rec_worker",
                                    "图片生成状态": "已提交",
                                }
                            ),
                        }
                    ],
                    "write_back": True,
                    "limit": 10,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_pending")
        self.assertEqual(body["results"][0]["blocking"][0]["code"], "IMAGE_RESULT_WRITEBACK_DISABLED")
        self.assertTrue(ingest.await_args.args[0].write_back)

    def test_publish_scan_selects_due_records_and_runs_dry_run(self):
        client = TestClient(app)
        with patch.object(main_module, "_load_account", new_callable=AsyncMock) as load_account:
            load_account.return_value = publish_account()
            resp = client.post(
                "/publish/scan",
                json={
                    "records": [
                        {"record_id": "rec_due", "fields": publish_ready_fields()},
                        {
                            "record_id": "rec_future",
                            "fields": publish_ready_fields(**{"计划发布时间": "2026-07-02 10:00:00"}),
                        },
                        {"record_id": "rec_unapproved", "fields": publish_ready_fields(**{"审批通过": False})},
                    ],
                    "commit": False,
                    "now": "2026-07-01T10:00:00+00:00",
                    "limit": 10,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["dry_run_passed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_due")
        self.assertEqual(body["results"][0]["dry_run"]["status"], "dry-run-pass")
        self.assertEqual(body["skipped_sample"][0]["record_id"], "rec_future")
        self.assertEqual(body["skipped_sample"][0]["reason"], "schedule_not_due")

    def test_publish_scan_commit_still_obeys_commit_gate(self):
        client = TestClient(app)
        with patch.object(main_module, "_load_account", new_callable=AsyncMock) as load_account:
            load_account.return_value = publish_account()
            resp = client.post(
                "/publish/scan",
                json={
                    "records": [{"record_id": "rec_due", "fields": publish_ready_fields()}],
                    "commit": True,
                    "now": "2026-07-01T10:00:00+00:00",
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["dry_run_passed"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["commit"]["blocking"][0]["code"], "COMMIT_DISABLED")

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

    def test_generate_scan_skips_missing_product_reference(self):
        client = TestClient(app)
        resp = client.post(
            "/generate/scan",
            json={
                "records": [{"record_id": "rec_missing_ref", "fields": fields(**{"产品参考图": []})}],
                "write_back": False,
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["generated"], 0)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["skipped_sample"][0]["reason"], "missing_product_reference_image")

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

    def test_generate_scan_source_failure_returns_structured_error(self):
        client = TestClient(app)
        feishu = AsyncMock()
        feishu.list_records.side_effect = RuntimeError("source down")
        with patch.object(main_module, "_feishu", return_value=feishu), patch.object(
            main_module, "_write_log", new_callable=AsyncMock
        ) as write_log:
            resp = client.post(
                "/generate/scan",
                json={"write_back": False, "source": "replay", "limit": 3},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "scan-source-failed")
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "__scan__")
        self.assertIn("SCAN_SOURCE_FAILED: RuntimeError", body["results"][0]["reason"])
        write_log.assert_awaited_once()

    def test_generate_scan_candidate_exception_is_record_scoped(self):
        client = TestClient(app)
        with patch.object(main_module, "_write_log", new_callable=AsyncMock), patch.object(
            main_module,
            "_enrich_content_fields_with_product_context",
            new_callable=AsyncMock,
        ) as enrich:
            enrich.side_effect = RuntimeError("bad product")
            resp = client.post(
                "/generate/scan",
                json={"records": [{"record_id": "rec_bad", "fields": fields()}], "write_back": False},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["selected"], 0)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_bad")
        self.assertIn("SCAN_RECORD_FAILED: RuntimeError", body["results"][0]["reason"])

    def test_generate_scan_generate_exception_is_record_scoped(self):
        client = TestClient(app)
        with patch.object(main_module, "_write_log", new_callable=AsyncMock), patch.object(
            main_module, "_execute_generate", new_callable=AsyncMock
        ) as execute_generate:
            execute_generate.side_effect = RuntimeError("bad generate")
            resp = client.post(
                "/generate/scan",
                json={"records": [{"record_id": "rec_bad", "fields": fields()}], "write_back": False},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["results"][0]["record_id"], "rec_bad")
        self.assertIn("SCAN_RECORD_FAILED: RuntimeError", body["results"][0]["reason"])

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
