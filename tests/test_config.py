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

    def test_social_crm_p0_configuration_flags_require_complete_credentials(self):
        settings = Settings(
            feishu_bitable_app_id="bitable-app",
            feishu_bitable_app_secret="bitable-secret",
            social_crm_p0_base_token="base-token",
            social_crm_p0_post_table_id="post-table",
            social_crm_p0_snapshot_table_id="snapshot-table",
            social_crm_youtube_oauth_client_json="{}",
            social_crm_youtube_token_funlab_json="{}",
            social_crm_youtube_token_powkong_json="{}",
            social_crm_x_client_funlab_json="{}",
            social_crm_x_token_funlab_json="{}",
            social_crm_x_client_powkong_json="{}",
            social_crm_x_token_powkong_json="{}",
        )

        self.assertTrue(settings.social_crm_p0_base_enabled())
        self.assertTrue(settings.social_crm_p0_youtube_enabled())
        self.assertTrue(settings.social_crm_p0_x_enabled())

        incomplete = Settings(
            feishu_bitable_app_id="bitable-app",
            feishu_bitable_app_secret="bitable-secret",
            social_crm_p0_base_token="base-token",
            social_crm_p0_post_table_id="post-table",
            social_crm_p0_snapshot_table_id="snapshot-table",
            social_crm_youtube_oauth_client_json="{}",
            social_crm_youtube_token_funlab_json="{}",
            social_crm_youtube_token_powkong_json="",
            social_crm_x_client_funlab_json="{}",
            social_crm_x_token_funlab_json="{}",
            social_crm_x_client_powkong_json="",
            social_crm_x_token_powkong_json="{}",
        )

        self.assertTrue(incomplete.social_crm_p0_base_enabled())
        self.assertFalse(incomplete.social_crm_p0_youtube_enabled())
        self.assertFalse(incomplete.social_crm_p0_x_enabled())


if __name__ == "__main__":
    unittest.main()
