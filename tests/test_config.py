import unittest

from app.config import Settings


class SettingsMetaTokenTest(unittest.TestCase):
    def test_brand_specific_meta_token_takes_precedence(self):
        settings = Settings(
            meta_access_token="global-token",
            meta_access_token_powkong="powkong-token",
            meta_access_token_funlab="funlab-token",
        )

        self.assertEqual(settings.meta_token_for_brand("Powkong"), "powkong-token")
        self.assertEqual(settings.meta_token_for_brand("FUNLAB"), "funlab-token")
        self.assertEqual(settings.meta_token_for_brand("unknown"), "global-token")

    def test_meta_enabled_accepts_brand_specific_tokens(self):
        settings = Settings(meta_access_token="", meta_access_token_powkong="powkong-token")

        self.assertTrue(settings.meta_enabled())
        self.assertEqual(settings.meta_token_for_brand("Powkong"), "powkong-token")
        self.assertEqual(settings.meta_token_for_brand("FUNLAB"), "")


if __name__ == "__main__":
    unittest.main()
