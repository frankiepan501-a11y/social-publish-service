import argparse
import unittest
from unittest.mock import patch

from scripts.remote_generation_readiness import check_health


def args(**overrides):
    base = {
        "url": "https://example.test",
        "token": "",
        "expect_writeback_enabled": False,
        "allow_publish_commit_enabled": False,
        "expect_ai_configured": False,
        "allow_feishu_unconfigured": False,
        "expect_meta_configured": False,
        "report_path": "",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class RemoteReadinessTest(unittest.TestCase):
    def test_health_accepts_safe_defaults(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": True,
            "image_task_configured": True,
            "meta_configured": False,
            "generation_ai_provider": "template",
            "generation_ai_configured": False,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            self.assertEqual(check_health(args()), payload)

    def test_health_requires_feishu_by_default(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": False,
            "image_task_configured": False,
            "meta_configured": False,
            "generation_ai_provider": "template",
            "generation_ai_configured": False,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            with self.assertRaisesRegex(AssertionError, "FEISHU_"):
                check_health(args())

    def test_health_can_allow_missing_feishu_for_inline_only_smoke(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": False,
            "image_task_configured": False,
            "meta_configured": False,
            "generation_ai_provider": "template",
            "generation_ai_configured": False,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            self.assertEqual(check_health(args(allow_feishu_unconfigured=True)), payload)

    def test_health_requires_meta_when_requested(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": True,
            "image_task_configured": True,
            "meta_configured": False,
            "generation_ai_provider": "template",
            "generation_ai_configured": False,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            with self.assertRaisesRegex(AssertionError, "META_ACCESS_TOKEN"):
                check_health(args(expect_meta_configured=True))

    def test_health_requires_ai_when_requested(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": True,
            "image_task_configured": True,
            "meta_configured": False,
            "generation_ai_provider": "template",
            "generation_ai_configured": False,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            with self.assertRaisesRegex(AssertionError, "GENERATION_AI_API_KEY"):
                check_health(args(expect_ai_configured=True))

    def test_health_accepts_live_ai_when_requested(self):
        payload = {
            "ok": True,
            "commit_enabled": False,
            "generation_writeback_enabled": False,
            "image_task_write_enabled": False,
            "image_result_writeback_enabled": False,
            "asset_prepare_enabled": False,
            "feishu_configured": True,
            "image_task_configured": True,
            "meta_configured": False,
            "generation_ai_provider": "deepseek",
            "generation_ai_configured": True,
        }
        with patch("scripts.remote_generation_readiness.request_json", return_value=(200, payload)):
            self.assertEqual(check_health(args(expect_ai_configured=True)), payload)


if __name__ == "__main__":
    unittest.main()
