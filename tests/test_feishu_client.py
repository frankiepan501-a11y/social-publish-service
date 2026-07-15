import unittest

from app.feishu_client import _normalize_bitable_fields


class FeishuClientTests(unittest.TestCase):
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
