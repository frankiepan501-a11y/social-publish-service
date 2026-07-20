from __future__ import annotations

import asyncio

import httpx


class MetaApiError(RuntimeError):
    def __init__(self, message: str, code: str = "META_API_ERROR"):
        super().__init__(message)
        self.code = code


class MetaClient:
    def __init__(self, access_token: str, graph_version: str = "v25.0"):
        if not access_token:
            raise MetaApiError("META_ACCESS_TOKEN is not configured", "META_TOKEN_MISSING")
        self._access_token = access_token
        self._base = f"https://graph.facebook.com/{graph_version}"

    async def _request(self, method: str, path: str, **kwargs):
        params = kwargs.pop("params", {}) or {}
        data = kwargs.pop("data", {}) or {}
        token_target = data if method.upper() == "POST" else params
        token_target["access_token"] = self._access_token
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.request(method, f"{self._base}/{path.lstrip('/')}", params=params, data=data)
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in payload:
            err = payload.get("error", {})
            code = str(err.get("code") or response.status_code)
            message = err.get("message") or f"Meta API returned HTTP {response.status_code}"
            raise MetaApiError(message, code=code)
        return payload

    async def content_publishing_limit(self, ig_user_id: str) -> dict:
        return await self._request("GET", f"{ig_user_id}/content_publishing_limit")

    async def _wait_for_instagram_container(self, creation_id: str) -> dict:
        last_payload: dict = {}
        for attempt in range(1, 11):
            payload = await self._request("GET", creation_id, params={"fields": "id,status_code,status"})
            last_payload = payload
            status_code = str(payload.get("status_code") or "").upper()
            if status_code in {"FINISHED", "PUBLISHED"}:
                return payload
            if status_code == "ERROR":
                raise MetaApiError(
                    str(payload.get("status") or "Instagram media container processing failed"),
                    "IG_CONTAINER_ERROR",
                )
            await asyncio.sleep(min(3 * attempt, 15))
        raise MetaApiError(
            f"Instagram media container was not ready for publish: {last_payload}",
            "IG_CONTAINER_NOT_READY",
        )

    async def publish_instagram_image(self, ig_user_id: str, image_url: str, caption: str) -> dict:
        container = await self._request(
            "POST",
            f"{ig_user_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        creation_id = container["id"]
        await self._wait_for_instagram_container(creation_id)
        published = await self._publish_instagram_container(ig_user_id, creation_id)
        media_id = published["id"]
        media = await self._request("GET", media_id, params={"fields": "id,permalink,media_type,timestamp"})
        return {"creation_id": creation_id, "media_id": media_id, "permalink": media.get("permalink", ""), "raw": media}

    async def publish_instagram_carousel(self, ig_user_id: str, image_urls: list[str], caption: str) -> dict:
        child_ids = []
        for image_url in image_urls:
            child = await self._request(
                "POST",
                f"{ig_user_id}/media",
                data={"image_url": image_url, "is_carousel_item": "true"},
            )
            child_ids.append(child["id"])
        for child_id in child_ids:
            await self._wait_for_instagram_container(child_id)
        container = await self._request(
            "POST",
            f"{ig_user_id}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption},
        )
        creation_id = container["id"]
        await self._wait_for_instagram_container(creation_id)
        published = await self._publish_instagram_container(ig_user_id, creation_id)
        media_id = published["id"]
        media = await self._request("GET", media_id, params={"fields": "id,permalink,media_type,timestamp"})
        return {
            "creation_id": creation_id,
            "media_id": media_id,
            "permalink": media.get("permalink", ""),
            "children": child_ids,
            "raw": media,
        }

    async def _publish_instagram_container(self, ig_user_id: str, creation_id: str) -> dict:
        last_error: MetaApiError | None = None
        for attempt in range(1, 7):
            try:
                return await self._request("POST", f"{ig_user_id}/media_publish", data={"creation_id": creation_id})
            except MetaApiError as exc:
                if exc.code != "9007":
                    raise
                last_error = exc
                await asyncio.sleep(min(10 * attempt, 45))
                await self._wait_for_instagram_container(creation_id)
        assert last_error is not None
        raise last_error

    async def publish_facebook_photo(self, page_id: str, image_url: str, caption: str) -> dict:
        photo = await self._request(
            "POST",
            f"{page_id}/photos",
            data={"url": image_url, "caption": caption, "published": "true"},
        )
        photo_id = photo["id"]
        fields = await self._request("GET", photo_id, params={"fields": "id,link"})
        return {
            "photo_id": photo_id,
            "post_id": photo.get("post_id", ""),
            "permalink": fields.get("link", ""),
            "raw": fields,
        }

    async def stage_facebook_photo_url(
        self,
        page_id: str,
        image_bytes: bytes,
        *,
        filename: str = "fb_ig_asset.png",
        content_type: str = "image/png",
    ) -> dict:
        data = {"published": "false", "access_token": self._access_token}
        files = {"source": (filename, image_bytes, content_type)}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{self._base}/{page_id}/photos", data=data, files=files)
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in payload:
            err = payload.get("error", {})
            code = str(err.get("code") or response.status_code)
            message = err.get("message") or f"Meta API returned HTTP {response.status_code}"
            raise MetaApiError(message, code=code)
        photo_id = payload["id"]
        fields = await self._request("GET", photo_id, params={"fields": "id,images,link"})
        images = fields.get("images") or []
        image_url = ""
        if images:
            image_url = str(images[0].get("source") or "")
        image_url = image_url or str(fields.get("link") or "")
        if not image_url:
            raise MetaApiError("Staged Facebook photo did not return a public image URL", "ASSET_STAGE_URL_MISSING")
        return {"photo_id": photo_id, "image_url": image_url, "raw": fields}

    async def ig_media_insights(self, media_id: str) -> dict:
        return await self._request(
            "GET",
            f"{media_id}/insights",
            params={"metric": "reach,likes,comments,shares,saved,total_interactions"},
        )

    async def fb_post_insights(self, post_id: str) -> dict:
        return await self._request(
            "GET",
            f"{post_id}/insights",
            params={"metric": "post_impressions,post_engaged_users,post_clicks,post_reactions_by_type_total"},
        )

