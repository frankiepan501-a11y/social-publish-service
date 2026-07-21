import unittest

from app.social_crm_p0 import fallback_platform_part, parse_base_v3_records_page, upsert_rows


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


if __name__ == "__main__":
    unittest.main()
