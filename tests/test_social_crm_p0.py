import unittest

from app.social_crm_p0 import fallback_platform_part


class SocialCrmP0FallbackTest(unittest.TestCase):
    def test_x_fallback_keeps_brand_status_rows(self):
        rows, summaries, evidence = fallback_platform_part("x", "x timeout")

        self.assertEqual(["FUNLAB", "POWKONG"], [row["品牌"] for row in rows])
        self.assertTrue(all(row["同步状态"] == "blocker" for row in rows))
        self.assertTrue(all(row["内容类型"] == "status_only" for row in rows))
        self.assertEqual(2, len(summaries))
        self.assertTrue(evidence["safe_output"])


if __name__ == "__main__":
    unittest.main()
