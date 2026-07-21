import unittest
from datetime import datetime, timedelta, timezone

from app.rules import AccountConfig, build_publish_caption, validate_publish


def account(**overrides):
    base = {
        "account_name": "Powkong IG",
        "brand": "Powkong",
        "platform": "Instagram",
        "publish_slots": ["IG Feed", "IG Carousel"],
        "meta_page_id": "page",
        "ig_user_id": "ig",
        "daily_limit": 1,
        "weekly_limit": 3,
        "min_interval_hours": 36,
        "enabled": True,
        "default_mode": "auto",
    }
    base.update(overrides)
    return AccountConfig(**base)


def fields(**overrides):
    base = {
        "状态": "待发布",
        "品牌": "Powkong",
        "平台": ["Instagram"],
        "发布位置": ["IG Feed"],
        "计划发布账号": "Powkong IG",
        "发布模式": "auto",
        "素材类型": "single_image",
        "主图URL": "https://example.com/image.jpg",
        "Caption EN": "Make your setup brighter with a compact Switch accessory.",
        "审批通过": True,
        "最终素材确认": True,
        "审批风险等级": "normal",
        "实验变量": "Hook",
        "计划发布时间": "2026-07-01 10:00:00",
    }
    base.update(overrides)
    return base


class PublishRulesTest(unittest.TestCase):
    def test_valid_single_image(self):
        result = validate_publish(fields(), account(), now=datetime(2026, 7, 1, 10, 1, tzinfo=timezone.utc), commit=True)
        self.assertTrue(result.ok, result.blocking)

    def test_publish_caption_appends_hashtag_en_to_normalized(self):
        record = fields(**{"Hashtag EN": "#Powkong #DockGen2 #GamingSetup"})
        result = validate_publish(record, account(), now=datetime(2026, 7, 1, 10, 1, tzinfo=timezone.utc), commit=True)
        self.assertTrue(result.ok, result.blocking)
        self.assertEqual(result.normalized["caption"], "Make your setup brighter with a compact Switch accessory.")
        self.assertEqual(result.normalized["hashtags"], "#Powkong #DockGen2 #GamingSetup")
        self.assertEqual(
            result.normalized["publish_caption"],
            "Make your setup brighter with a compact Switch accessory.\n\n#Powkong #DockGen2 #GamingSetup",
        )
        self.assertEqual(build_publish_caption(record), result.normalized["publish_caption"])

    def test_publish_caption_does_not_duplicate_existing_hashtag(self):
        result = validate_publish(
            fields(**{"Caption EN": "Already in the caption. #Powkong", "Hashtag EN": "#Powkong #DockGen2"}),
            account(),
            now=datetime(2026, 7, 1, 10, 1, tzinfo=timezone.utc),
            commit=True,
        )
        self.assertTrue(result.ok, result.blocking)
        self.assertEqual(result.normalized["publish_caption"], "Already in the caption. #Powkong\n\n#DockGen2")

    def test_instagram_caption_length_counts_hashtags(self):
        result = validate_publish(
            fields(**{"Caption EN": "a" * 2199, "Hashtag EN": "#xy"}),
            account(),
            now=datetime(2026, 7, 1, 10, 1, tzinfo=timezone.utc),
            commit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("CAPTION_TOO_LONG", [issue.code for issue in result.blocking])

    def test_caption_block_terms_include_hashtags(self):
        result = validate_publish(
            fields(**{"Hashtag EN": "#Mario"}),
            account(),
            now=datetime(2026, 7, 1, 10, 1, tzinfo=timezone.utc),
            commit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("CAPTION_BLOCK_TERM", [issue.code for issue in result.blocking])

    def test_missing_approval_blocks(self):
        result = validate_publish(fields(**{"审批通过": False}), account(), commit=True)
        self.assertFalse(result.ok)
        self.assertIn("APPROVAL_MISSING", [issue.code for issue in result.blocking])

    def test_high_risk_requires_second_review(self):
        result = validate_publish(fields(**{"审批风险等级": "high-risk"}), account(), commit=True)
        self.assertFalse(result.ok)
        self.assertIn("SECOND_REVIEW_MISSING", [issue.code for issue in result.blocking])

    def test_carousel_requires_two_to_ten_assets(self):
        result = validate_publish(
            fields(**{"素材类型": "carousel", "发布位置": ["IG Carousel"], "Carousel素材URL": "https://example.com/a.jpg"}),
            account(),
            commit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("CAROUSEL_COUNT", [issue.code for issue in result.blocking])

    def test_frequency_min_interval_blocks(self):
        now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        recent = [
            fields(
                **{
                    "实际发布时间": (now - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"),
                    "计划发布账号": "Powkong IG",
                }
            )
        ]
        result = validate_publish(fields(), account(), recent_records=recent, now=now, commit=True)
        self.assertFalse(result.ok)
        codes = [issue.code for issue in result.blocking]
        self.assertIn("MIN_INTERVAL", codes)
        self.assertIn("DAILY_LIMIT", codes)

    def test_caption_block_terms(self):
        result = validate_publish(fields(**{"Caption EN": "Official Nintendo style Mario accessory."}), account(), commit=True)
        self.assertFalse(result.ok)
        self.assertIn("CAPTION_BLOCK_TERM", [issue.code for issue in result.blocking])

    def test_offer_weekly_limit(self):
        now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        old = fields(
            **{
                "实际发布时间": (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "计划发布账号": "Powkong IG",
                "权益内容": True,
            }
        )
        result = validate_publish(fields(**{"权益内容": True}), account(), recent_records=[old], now=now, commit=True)
        self.assertFalse(result.ok)
        self.assertIn("OFFER_WEEKLY_LIMIT", [issue.code for issue in result.blocking])

    def test_file_token_can_stand_in_for_public_url_before_commit_prepare(self):
        result = validate_publish(
            fields(**{"主图URL": "", "素材链接": "", "AI生成图链接": "", "生成图片file_token": "filetoken_123"}),
            account(),
            commit=True,
        )
        self.assertTrue(result.ok, result.blocking)
        self.assertEqual(result.normalized["asset_file_tokens"], ["filetoken_123"])
        self.assertEqual(result.normalized["asset_urls"], [])
        self.assertIn("ASSET_PUBLIC_URL_PENDING", [issue.code for issue in result.warnings])

    def test_feishu_drive_url_is_not_publishable_asset_url(self):
        result = validate_publish(
            fields(**{"主图URL": "https://u1wpma3xuhr.feishu.cn/drive/folder/folder_token"}),
            account(),
            commit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("ASSET_URL_INVALID", [issue.code for issue in result.blocking])


if __name__ == "__main__":
    unittest.main()
