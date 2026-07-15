import json
import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app


def approval_record(**overrides):
    fields = {
        "内容标题": "FUNLAB handheld dark desk visual",
        "品牌": "FUNLAB",
        "平台": ["Instagram"],
        "产品名": "FUNLAB FF01A-04 Controller",
        "品牌型号/SKU": "FF01A-04",
        "实验变量": "visual_recipe",
        "图片生成状态": "已生成",
        "Caption EN": "A darker desk setup for late-night Switch sessions.",
        "Hashtag EN": "#funlab #switchcontroller",
        "重生版本号": 1,
        "图片任务record_id": "rec_img_task",
        "生成图片file_token": "file_token_old",
        "public_asset_url": "https://cdn.example.com/old.png",
        "FB Staged Photo ID": "staged_old",
        "AI图片Prompt": "Close-up handheld product photo on a dark wood desk.",
        "图片生成模式": "Codex Image",
        "场景模板": "FB/INS广告图",
        "产品参考图": [{"file_token": "ref_funlab", "name": "funlab.png"}],
        "IP合规状态": "合规-无IP",
    }
    fields.update(overrides)
    return {"record_id": "rec_funlab_approval", "fields": fields}


class ApprovalCallbackTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            dry_run_write_logs=False,
            feishu_app_id="",
            feishu_app_secret="",
            feishu_base_token="",
            image_task_base_token="",
            product_library_base_token="",
        )
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_card_preview_exposes_12_dimension_feedback_schema(self):
        resp = self.client.post("/approval/card-preview", json={"record": approval_record()})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        card = data["card"]
        self.assertEqual(card["feedback_schema_version"], "v3-12-dimensions")
        self.assertEqual(card["feedback_options"], [])
        self.assertEqual(len(card["feedback_dimensions"]), 12)
        field_names = {item["field_name"] for item in card["feedback_dimensions"]}
        self.assertIn("图片反馈-产品保真", field_names)
        self.assertIn("图片反馈-镜头视角", field_names)
        self.assertIn("图片反馈-道具元素", field_names)
        hard_rule_tags = {item["tag"] for item in card["brand_hard_rules"]}
        self.assertIn("FUNLAB_HIDDEN_EMISSIVE_PATTERN", hard_rule_tags)

    def test_regenerate_image_builds_dimension_level_patch(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record": approval_record(),
                "action": "regenerate_image",
                "feedback_text": "手握方向要贴近原图。",
                "feedback_dimensions": {
                    "产品保真": "按键接口不对",
                    "镜头视角": "产品朝向不对",
                    "道具元素": "手部姿势不自然",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "approval-dry-run")

        fields = data["fields"]
        self.assertEqual(fields["图片生成状态"], "待生成")
        self.assertEqual(fields["重生版本号"], 2)
        self.assertEqual(fields["图片任务record_id"], "")
        self.assertEqual(fields["生成图片file_token"], "")
        self.assertIn("产品保真=按键接口不对", fields["图片反馈标签"])
        self.assertIn("镜头视角=产品朝向不对", fields["图片反馈标签"])
        self.assertIn("道具元素=手部姿势不自然", fields["图片反馈标签"])
        self.assertEqual(fields["图片反馈-产品保真"], "按键接口不对")
        self.assertEqual(fields["图片反馈-镜头视角"], "产品朝向不对")
        self.assertEqual(fields["图片反馈-道具元素"], "手部姿势不自然")
        self.assertEqual(fields["图片反馈-灯光"], "不改")

        patch = json.loads(fields["图片重生Patch"])
        elements = {item["element"] for item in patch["change"]}
        self.assertIn("product_fidelity", elements)
        self.assertIn("camera_angle", elements)
        self.assertIn("props", elements)
        self.assertIn("brand_hard_rule_funlab_hidden_emissive_pattern", elements)
        self.assertIn("operator_feedback", elements)
        hard_rule_tags = {item["tag"] for item in patch["brand_hard_rules"]}
        self.assertIn("FUNLAB_HIDDEN_EMISSIVE_PATTERN", hard_rule_tags)

    def test_legacy_feedback_tags_still_build_patch_for_old_callbacks(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record": approval_record(),
                "action": "regenerate_image",
                "feedback_tags": ["POSE_ORIENTATION_MISMATCH", "EMISSIVE_PATTERN_MISSING"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        patch = json.loads(data["fields"]["图片重生Patch"])
        elements = {item["element"] for item in patch["change"]}
        self.assertIn("pose_orientation", elements)
        self.assertIn("emissive_pattern", elements)
        self.assertIn("brand_hard_rule_funlab_hidden_emissive_pattern", elements)

    def test_regenerate_image_can_build_followup_image_task_prompt(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record": approval_record(),
                "action": "regenerate_image",
                "create_image_task": True,
                "feedback_dimensions": {
                    "镜头视角": "产品朝向不对",
                    "灯光": "产品自发光不足",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        image_task = data["image_task"]
        self.assertTrue(image_task["ok"])
        prompt = image_task["task_fields"]["自定义提示词"]
        self.assertIn("Regeneration instructions from the approval card", prompt)
        self.assertIn("camera_angle", prompt)
        self.assertIn("lighting", prompt)
        self.assertIn("brand_hard_rule_funlab_hidden_emissive_pattern", prompt)
        self.assertIn("Structured feedback patch JSON", prompt)

    def test_regenerate_copy_can_write_operator_copy_overrides(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record": approval_record(),
                "action": "regenerate_copy",
                "feedback_text": "运营直接改了文案。",
                "copy_overrides": {
                    "caption_en": "A refined late-night setup with the exact FUNLAB controller glow.",
                    "hashtag_en": "#FUNLAB #HiddenGlow #SwitchController",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        fields = data["fields"]
        self.assertEqual(fields["Caption EN"], "A refined late-night setup with the exact FUNLAB controller glow.")
        self.assertEqual(fields["Hashtag EN"], "#FUNLAB #HiddenGlow #SwitchController")
        self.assertTrue(fields["文案人工锁定"])
        self.assertTrue(fields["图片Prompt人工锁定"])
        self.assertEqual(fields["文案修改意见"], "运营直接改了文案。")
        self.assertNotIn("AI生成状态", fields)

    def test_regenerate_both_combines_image_feedback_and_copy_overrides(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record": approval_record(),
                "action": "regenerate_both",
                "feedback_text": "手握方向不对。",
                "copy_overrides": {
                    "caption_en_override": "The glow stays precise while the setup gets darker and cleaner.",
                },
                "feedback_dimensions": {
                    "产品保真": "材质不对",
                    "镜头视角": "产品朝向不对",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        fields = data["fields"]
        self.assertEqual(fields["图片生成状态"], "待生成")
        self.assertEqual(fields["图片反馈-产品保真"], "材质不对")
        self.assertEqual(fields["图片反馈-镜头视角"], "产品朝向不对")
        self.assertEqual(fields["Caption EN"], "The glow stays precise while the setup gets darker and cleaner.")
        self.assertTrue(fields["文案人工锁定"])
        self.assertEqual(fields["文案修改意见"], "手握方向不对。")
        self.assertNotIn("AI生成状态", fields)

    def test_writeback_is_blocked_until_explicitly_enabled(self):
        resp = self.client.post(
            "/approval/action",
            json={
                "record_id": "rec_funlab_approval",
                "record": approval_record(),
                "action": "regenerate_image",
                "write_back": True,
                "feedback_tags": ["EMISSIVE_PATTERN_MISSING"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["blocking"][0]["code"], "APPROVAL_WRITEBACK_DISABLED")


if __name__ == "__main__":
    unittest.main()
