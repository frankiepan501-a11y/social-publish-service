import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app
from app.social_crm_p1 import build_social_crm_p1_fields, social_crm_p1_precheck
from app.models import SocialCrmP1PublishRequest


def crm_record(**overrides):
    fields = {
        "品牌": "Powkong",
        "平台": "Instagram",
        "账号": "@powkong_official",
        "草稿状态": "已排期",
        "审批结果": "通过",
        "最终素材确认": "通过",
        "发帖文案": "Keep your desk clean with a compact charging setup.",
        "话题标签": "#Powkong #GamingSetup",
        "图片URL": "https://cdn.example.com/powkong-desk.png",
        "计划发布时间": "2026-07-01 10:00:00",
    }
    fields.update(overrides)
    return {"record_id": "rec_social_crm_p1", "fields": fields}


def account_config(**overrides):
    fields = {
        "账号名称": "POWKONG IG",
        "品牌": "Powkong",
        "平台": "Instagram",
        "发布位置": ["IG Feed"],
        "Meta Page ID": "page-id",
        "Instagram User ID": "ig-user-id",
        "每日上限": 1,
        "每周上限": 3,
        "最小间隔小时": 36,
        "自动发布启用": True,
        "默认发布模式": "auto",
    }
    fields.update(overrides)
    return {"fields": fields}


class SocialCrmP1MappingTest(unittest.TestCase):
    def test_maps_social_crm_fields_to_publish_record(self):
        mapped = build_social_crm_p1_fields(crm_record()["fields"])

        self.assertEqual(mapped["状态"], "待发布")
        self.assertEqual(mapped["平台"], ["Instagram"])
        self.assertEqual(mapped["发布位置"], ["IG Feed"])
        self.assertEqual(mapped["计划发布账号"], "POWKONG IG")
        self.assertTrue(mapped["审批通过"])
        self.assertTrue(mapped["最终素材确认"])
        self.assertEqual(mapped["Caption EN"], "Keep your desk clean with a compact charging setup.")
        self.assertEqual(mapped["Hashtag EN"], "#Powkong #GamingSetup")
        self.assertEqual(mapped["主图URL"], "https://cdn.example.com/powkong-desk.png")
        self.assertEqual(mapped["实验变量"], "Social CRM P1 canary")

    def test_unsupported_platform_is_blocked_before_publish(self):
        mapped = build_social_crm_p1_fields(crm_record(**{"平台": "X"})["fields"])
        issues = social_crm_p1_precheck(
            SocialCrmP1PublishRequest(record=crm_record(**{"平台": "X"}), canary=True),
            mapped,
            commit=False,
            p1_publish_enabled=False,
        )

        self.assertIn("PLATFORM_NOT_P1", [item["code"] for item in issues])

    def test_commit_requires_canary_manual_p1_gate_and_auth_time(self):
        mapped = build_social_crm_p1_fields(crm_record()["fields"])
        issues = social_crm_p1_precheck(
            SocialCrmP1PublishRequest(record=crm_record(), source="auto", canary=False),
            mapped,
            commit=True,
            p1_publish_enabled=False,
        )

        codes = [item["code"] for item in issues]
        self.assertIn("CANARY_REQUIRED", codes)
        self.assertIn("MANUAL_SOURCE_REQUIRED", codes)
        self.assertIn("P1_PUBLISH_DISABLED", codes)
        self.assertIn("AUTHORIZATION_TIME_MISSING", codes)


class SocialCrmP1EndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            meta_access_token="",
            meta_access_token_powkong="",
            meta_access_token_funlab="",
            feishu_app_id="",
            feishu_app_secret="",
            feishu_bitable_app_id="",
            feishu_bitable_app_secret="",
            dry_run_write_logs=False,
        )

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_dry_run_reuses_publish_validation(self):
        resp = self.client.post(
            "/social-crm-p1/publish/dry-run",
            json={
                "record": crm_record(),
                "account_config": account_config(),
                "now": "2026-07-01 10:01:00",
                "canary": True,
            },
        )
        data = resp.json()

        self.assertEqual(200, resp.status_code)
        self.assertTrue(data["ok"], data)
        self.assertEqual("dry-run-pass", data["status"])
        self.assertEqual("POWKONG IG", data["social_crm_p1"]["mapped_fields"]["计划发布账号"])
        self.assertEqual("pass", data["social_crm_p1"]["writeback_hint"]["dry-run 结果"].split(";")[0])

    def test_commit_endpoint_blocks_when_p1_gate_is_disabled(self):
        resp = self.client.post(
            "/social-crm-p1/publish/commit",
            json={
                "record": crm_record(**{"真实发布授权时间": "2026-07-01 10:00:30"}),
                "account_config": account_config(),
                "now": "2026-07-01 10:01:00",
                "source": "manual",
                "canary": True,
            },
        )
        data = resp.json()

        self.assertEqual(200, resp.status_code)
        self.assertFalse(data["ok"])
        self.assertEqual("blocked", data["status"])
        self.assertIn("P1_PUBLISH_DISABLED", [item["code"] for item in data["blocking"]])


class SettingsSocialCrmP1Test(unittest.TestCase):
    def test_p1_publish_configured_requires_meta_and_feishu(self):
        settings = Settings(
            meta_access_token_powkong="meta-token",
            feishu_bitable_app_id="app-id",
            feishu_bitable_app_secret="app-secret",
            feishu_base_token="base-token",
        )

        self.assertTrue(settings.social_crm_p1_publish_configured())
        self.assertFalse(settings.social_crm_p1_publish_enabled)


if __name__ == "__main__":
    unittest.main()
