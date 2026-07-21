import unittest

from app.social_crm_p0 import fallback_platform_part, upsert_rows


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

    async def batch_update_records(self, table_id, records):
        self.updated.extend(records)
        return records

    async def batch_create_records(self, table_id, records):
        self.created.extend(records)
        return records


class SocialCrmP0UpsertTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_uses_batch_create_and_update(self):
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


if __name__ == "__main__":
    unittest.main()
