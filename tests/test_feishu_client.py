import unittest

from app.feishu_client import _normalize_bitable_fields


class FeishuClientTests(unittest.TestCase):
    def test_normalize_bitable_fields_converts_datetime_strings_to_ms(self):
        fields = {
            "AI生成时间": "2026-07-02 04:46:52",
            "发生时间": "2026-07-02 04:46",
            "Caption EN": "hello",
        }

        normalized = _normalize_bitable_fields(fields)

        self.assertEqual(normalized["AI生成时间"], 1782967612000)
        self.assertEqual(normalized["发生时间"], 1782967560000)
        self.assertEqual(normalized["Caption EN"], "hello")
        self.assertEqual(fields["AI生成时间"], "2026-07-02 04:46:52")
