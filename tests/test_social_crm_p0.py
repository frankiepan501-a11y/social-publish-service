import unittest
from unittest.mock import patch

from app.config import Settings
from app.social_crm_p0 import fallback_platform_part, parse_base_v3_records_page, persist_x_tokens_to_zeabur, upsert_rows


class SocialCrmP0FallbackTest(unittest.TestCase):
    def test_x_fallback_keeps_brand_status_rows(self):
        rows, summaries, evidence = fallback_platform_part("x", "x timeout")

        self.assertEqual(["FUNLAB", "POWKONG"], [row["品牌"] for row in rows])
        self.assertTrue(all(row["同步状态"] == "blocker" for row in rows))
        self.assertTrue(all(row["内容类型"] == "status_only" for row in rows))
        self.assertEqual(2, len(summaries))
        self.assertTrue(evidence["safe_output"])


class FakeBaseClient:
    def __init__(self):
        self.updated = []
        self.created = []

    async def list_records(self, table_id):
        return [{"record_id": "rec_existing", "fields": {"同步键": "existing"}}]

    async def update_record(self, table_id, record_id, fields):
        self.updated.append({"record_id": record_id, "fields": fields})
        return {"record_id": record_id, "fields": fields}

    async def create_record(self, table_id, fields):
        self.created.append({"fields": fields})
        return {"record_id": "rec_new", "fields": fields}


class SocialCrmP0UpsertTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_updates_existing_and_creates_missing(self):
        client = FakeBaseClient()

        counts = await upsert_rows(
            client,
            "tbl",
            "同步键",
            [
                {"同步键": "existing", "品牌": "FUNLAB"},
                {"同步键": "new", "品牌": "POWKONG"},
            ],
            True,
        )

        self.assertEqual({"created": 1, "updated": 1, "planned": 2}, counts)
        self.assertEqual("rec_existing", client.updated[0]["record_id"])
        self.assertEqual("POWKONG", client.created[0]["fields"]["品牌"])


class SocialCrmP0BaseV3ParseTest(unittest.TestCase):
    def test_parse_base_v3_records_page_field_matrix_shape(self):
        records = parse_base_v3_records_page(
            {
                "fields": ["同步键", "品牌"],
                "record_id_list": ["rec1", "rec2"],
                "data": [["key1", ["FUNLAB"]], ["key2", ["POWKONG"]]],
            }
        )

        self.assertEqual("rec1", records[0]["record_id"])
        self.assertEqual("key1", records[0]["fields"]["同步键"])
        self.assertEqual(["POWKONG"], records[1]["fields"]["品牌"])


class SocialCrmP0XZeaburPersistTest(unittest.IsolatedAsyncioTestCase):
    async def test_persist_x_tokens_writes_expected_env_keys(self):
        calls = []

        async def fake_upsert(settings, key, value):
            calls.append((key, value))
            return "updated"

        settings = Settings(
            social_crm_x_zeabur_env_persist_enabled=True,
            social_crm_x_zeabur_api_key="key",
            social_crm_x_zeabur_service_id="service",
            social_crm_x_zeabur_environment_id="env",
        )

        with patch("app.social_crm_p0.zeabur_upsert_env", new=fake_upsert):
            updated, errors = await persist_x_tokens_to_zeabur(
                settings,
                {"FUNLAB": '{"brand":"funlab"}', "POWKONG": '{"brand":"powkong"}'},
            )

        self.assertEqual([], errors)
        self.assertEqual(["FUNLAB:updated", "POWKONG:updated"], updated)
        self.assertEqual(
            [
                ("SOCIAL_CRM_X_TOKEN_FUNLAB_JSON", '{"brand":"funlab"}'),
                ("SOCIAL_CRM_X_TOKEN_POWKONG_JSON", '{"brand":"powkong"}'),
            ],
            calls,
        )

    async def test_persist_x_tokens_reports_missing_zeabur_config(self):
        settings = Settings(social_crm_x_zeabur_env_persist_enabled=True)

        updated, errors = await persist_x_tokens_to_zeabur(settings, {"FUNLAB": "{}"})

        self.assertEqual([], updated)
        self.assertEqual(["Zeabur env persistence is enabled but required Zeabur config is missing"], errors)


if __name__ == "__main__":
    unittest.main()
