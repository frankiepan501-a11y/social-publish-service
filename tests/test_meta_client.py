import unittest
from unittest.mock import AsyncMock, patch

from app.meta_client import MetaApiError, MetaClient


class FakeMetaClient(MetaClient):
    def __init__(self, responses):
        super().__init__("test-token", "v25.0")
        self.responses = list(responses)
        self.calls = []

    async def _request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if not self.responses:
            raise AssertionError("No fake response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MetaClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_image_waits_for_container_before_publish(self):
        client = FakeMetaClient(
            [
                {"id": "creation-1"},
                {"id": "creation-1", "status_code": "IN_PROGRESS"},
                {"id": "creation-1", "status_code": "FINISHED"},
                {"id": "media-1"},
                {"id": "media-1", "permalink": "https://instagram.example/p/1"},
            ]
        )

        result = await client.publish_instagram_image("ig-1", "https://example.com/image.jpg", "caption")

        self.assertEqual(result["creation_id"], "creation-1")
        self.assertEqual(result["media_id"], "media-1")
        self.assertEqual(
            [call[1] for call in client.calls],
            ["ig-1/media", "creation-1", "creation-1", "ig-1/media_publish", "media-1"],
        )

    async def test_instagram_container_error_is_explicit(self):
        client = FakeMetaClient(
            [
                {"id": "creation-1"},
                {"id": "creation-1", "status_code": "ERROR", "status": "bad image"},
            ]
        )

        with self.assertRaises(MetaApiError) as ctx:
            await client.publish_instagram_image("ig-1", "https://example.com/image.jpg", "caption")

        self.assertEqual(ctx.exception.code, "IG_CONTAINER_ERROR")

    async def test_instagram_publish_retries_transient_media_unavailable(self):
        client = FakeMetaClient(
            [
                {"id": "creation-1"},
                {"id": "creation-1", "status_code": "FINISHED"},
                MetaApiError("Media ID is not available", "9007"),
                {"id": "creation-1", "status_code": "FINISHED"},
                {"id": "media-1"},
                {"id": "media-1", "permalink": "https://instagram.example/p/1"},
            ]
        )

        with patch("app.meta_client.asyncio.sleep", new=AsyncMock()):
            result = await client.publish_instagram_image("ig-1", "https://example.com/image.jpg", "caption")

        self.assertEqual(result["media_id"], "media-1")
        self.assertEqual(
            [call[1] for call in client.calls],
            ["ig-1/media", "creation-1", "ig-1/media_publish", "creation-1", "ig-1/media_publish", "media-1"],
        )

    async def test_facebook_photo_lookup_avoids_removed_post_id_field(self):
        client = FakeMetaClient(
            [
                {"id": "photo-1"},
                {"id": "photo-1", "link": "https://facebook.example/photo.php?fbid=photo-1"},
            ]
        )

        result = await client.publish_facebook_photo("page-1", "https://example.com/image.jpg", "caption")

        self.assertEqual(result["photo_id"], "photo-1")
        self.assertEqual(result["post_id"], "")
        self.assertEqual(result["permalink"], "https://facebook.example/photo.php?fbid=photo-1")
        self.assertEqual(client.calls[1][2]["params"]["fields"], "id,link")


    async def test_instagram_media_insights_uses_saved_metric_name(self):
        client = FakeMetaClient([{"data": []}])

        result = await client.ig_media_insights("media-1")

        self.assertEqual(result, {"data": []})
        self.assertEqual(client.calls[0][1], "media-1/insights")
        metrics = client.calls[0][2]["params"]["metric"].split(",")
        self.assertIn("saved", metrics)
        self.assertNotIn("impressions", metrics)
        self.assertNotIn("saves", metrics)

    async def test_facebook_post_insights_uses_current_metric_names(self):
        client = FakeMetaClient([{"data": []}])

        result = await client.fb_post_insights("post-1")

        self.assertEqual(result, {"data": []})
        self.assertEqual(client.calls[0][1], "post-1/insights")
        metrics = client.calls[0][2]["params"]["metric"].split(",")
        self.assertIn("post_clicks", metrics)
        self.assertIn("post_clicks_by_type", metrics)
        self.assertIn("post_reactions_by_type_total", metrics)
        self.assertIn("post_activity_by_action_type", metrics)
        self.assertNotIn("post_impressions", metrics)
        self.assertNotIn("post_engaged_users", metrics)

    async def test_page_self_reads_linked_instagram_account(self):
        client = FakeMetaClient(
            [
                {
                    "id": "page-1",
                    "name": "Powkong",
                    "instagram_business_account": {"id": "ig-1", "username": "powkong"},
                }
            ]
        )

        result = await client.page_self()

        self.assertEqual(result["instagram_business_account"]["id"], "ig-1")
        self.assertEqual(client.calls[0][1], "me")
        self.assertEqual(client.calls[0][2]["params"]["fields"], "id,name,instagram_business_account{id,username}")

    async def test_debug_token_uses_input_token(self):
        client = FakeMetaClient([{"data": {"is_valid": True}}])

        result = await client.debug_token()

        self.assertTrue(result["data"]["is_valid"])
        self.assertEqual(client.calls[0][1], "debug_token")
        self.assertEqual(client.calls[0][2]["params"]["input_token"], "test-token")
if __name__ == "__main__":
    unittest.main()



