from __future__ import annotations

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

    async def publish_instagram_image(self, ig_user_id: str, image_url: str, caption: str) -> dict:
        container = await self._request(
            "POST",
            f"{ig_user_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        creation_id = container["id"]
        published = await self._request("POST", f"{ig_user_id}/media_publish", data={"creation_id": creation_id})
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
        container = await self._request(
            "POST",
            f"{ig_user_id}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption},
        )
        creation_id = container["id"]
        published = await self._request("POST", f"{ig_user_id}/media_publish", data={"creation_id": creation_id})
        media_id = published["id"]
        media = await self._request("GET", media_id, params={"fields": "id,permalink,media_type,timestamp"})
        return {
            "creation_id": creation_id,
            "media_id": media_id,
            "permalink": media.get("permalink", ""),
            "children": child_ids,
            "raw": media,
        }

    async def publish_facebook_photo(self, page_id: str, image_url: str, caption: str) -> dict:
        photo = await self._request(
            "POST",
            f"{page_id}/photos",
            data={"url": image_url, "caption": caption, "published": "true"},
        )
        photo_id = photo["id"]
        fields = await self._request("GET", photo_id, params={"fields": "id,post_id,link,permalink_url"})
        return {
            "photo_id": photo_id,
            "post_id": fields.get("post_id", ""),
            "permalink": fields.get("permalink_url") or fields.get("link", ""),
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
            params={"metric": "impressions,reach,likes,comments,shares,saves,total_interactions"},
        )

    async def fb_post_insights(self, post_id: str) -> dict:
        return await self._request(
            "GET",
            f"{post_id}/insights",
            params={"metric": "post_impressions,post_engaged_users,post_clicks,post_reactions_by_type_total"},
        )
