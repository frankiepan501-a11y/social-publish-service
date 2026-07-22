import unittest
from unittest.mock import patch

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


class HealthyMetaClient:
    def __init__(self, access_token, graph_version):
        self.access_token = access_token
        self.graph_version = graph_version

    async def debug_token(self):
        return {
            "data": {
                "is_valid": True,
                "type": "PAGE",
                "app_id": "app-1",
                "profile_id": "page-id",
                "scopes": [
                    "instagram_basic",
                    "instagram_content_publish",
                    "pages_manage_posts",
                    "pages_read_engagement",
                ],
            }
        }

    async def page_self(self, *, include_instagram=True):
        if not include_instagram:
            raise AssertionError("expected include_instagram=True")
        return {
            "id": "page-id",
            "name": "POWKONG",
            "instagram_business_account": {"id": "ig-user-id", "username": "powkong"},
        }

    async def instagram_user_basic(self, ig_user_id):
        return {"id": ig_user_id, "username": "powkong", "media_count": 12}

    async def content_publishing_limit(self, ig_user_id):
        return {"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]}


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
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            meta_access_token_powkong="meta-token",
            feishu_app_id="",
            feishu_app_secret="",
            feishu_bitable_app_id="",
            feishu_bitable_app_secret="",
            dry_run_write_logs=False,
        )

        with patch.object(main_module, "MetaClient", HealthyMetaClient):
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

    def test_dry_run_survives_feishu_recent_and_log_failures(self):
        class FailingFeishuClient:
            async def list_records(self, table_id, page_size=200):
                raise RuntimeError("legacy content table unavailable")

            async def append_run_log(self, table_id, **kwargs):
                raise RuntimeError("log table unavailable")

        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            meta_access_token="",
            meta_access_token_funlab="",
            feishu_app_id="app",
            feishu_app_secret="secret",
            feishu_base_token="base",
            content_table_id="tbl_content",
            log_table_id="tbl_log",
            dry_run_write_logs=True,
            meta_access_token_powkong="meta-token",
        )

        with patch.object(main_module, "_feishu", return_value=FailingFeishuClient()), patch.object(
            main_module, "MetaClient", HealthyMetaClient
        ):
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

    def test_p1_dry_run_blocks_when_meta_token_is_missing(self):
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
        self.assertFalse(data["ok"])
        self.assertEqual("blocked", data["status"])
        self.assertIn("META_TOKEN_MISSING", [item["code"] for item in data["blocking"]])
        self.assertFalse(data["meta_preflight"]["ok"])

    def test_dry_run_returns_meta_preflight_for_page_token(self):
        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            meta_access_token_powkong="meta-token",
            feishu_app_id="",
            feishu_app_secret="",
            feishu_bitable_app_id="",
            feishu_bitable_app_secret="",
            dry_run_write_logs=False,
        )

        with patch.object(main_module, "MetaClient", HealthyMetaClient):
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
        self.assertTrue(data["meta_preflight"]["ok"])
        self.assertEqual("page-id"[-5:], data["meta_preflight"]["checks"]["page_self"]["page_id_suffix"])
        self.assertEqual(50, data["meta_preflight"]["ig_limit"]["quota_total"])
        self.assertNotIn("META_PREFLIGHT_FAILED", [item["code"] for item in data["warnings"]])

    def test_commit_blocks_before_publish_when_page_token_mismatches_account_config(self):
        class MismatchMetaClient:
            def __init__(self, access_token, graph_version):
                self.access_token = access_token
                self.graph_version = graph_version

            async def debug_token(self):
                return {
                    "data": {
                        "is_valid": True,
                        "type": "PAGE",
                        "app_id": "app-1",
                        "profile_id": "other-page",
                        "scopes": ["instagram_basic", "instagram_content_publish", "pages_manage_posts"],
                    }
                }

            async def page_self(self, *, include_instagram=True):
                return {
                    "id": "other-page",
                    "name": "Other Page",
                    "instagram_business_account": {"id": "ig-user-id", "username": "powkong"},
                }

            async def instagram_user_basic(self, ig_user_id):
                return {"id": ig_user_id, "username": "powkong", "media_count": 12}

            async def content_publishing_limit(self, ig_user_id):
                return {"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]}

            async def publish_instagram_image(self, ig_user_id, image_url, caption):
                raise AssertionError("publish_instagram_image should not run when preflight blocks")

        app.dependency_overrides[main_module.get_settings] = lambda: Settings(
            service_token="",
            meta_access_token_powkong="meta-token",
            commit_enabled=True,
            social_crm_p1_publish_enabled=True,
            feishu_app_id="",
            feishu_app_secret="",
            feishu_bitable_app_id="",
            feishu_bitable_app_secret="",
            dry_run_write_logs=False,
        )

        with patch.object(main_module, "MetaClient", MismatchMetaClient):
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
        self.assertIn("META_PAGE_ID_MISMATCH", [item["code"] for item in data["blocking"]])


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
