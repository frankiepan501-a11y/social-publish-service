from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx

from .config import Settings
from .models import SocialCrmP0SyncRequest


SAFE_NOTE = "只写公开内容ID、链接、基础指标和同步状态；敏感凭据不在此表保存。"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_ME_URL = "https://api.x.com/2/users/me?user.fields=username,name,public_metrics,verified"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

META_RECORDS = [
    {
        "brand": "POWKONG",
        "platform": "Instagram",
        "record_id": "recvpwugmPPQoK",
        "url": "https://www.instagram.com/p/Da2OId8IE10/",
        "account": "@powkong_official",
        "content_type": "image",
    },
    {
        "brand": "POWKONG",
        "platform": "Facebook",
        "record_id": "recvpwugPD7xQO",
        "url": "https://www.facebook.com/photo.php?fbid=122203872062467548&set=a.122097563354467548&type=3",
        "account": "Powkong FB Page",
        "content_type": "page_photo",
    },
    {
        "brand": "FUNLAB",
        "platform": "Instagram",
        "record_id": "recvpwuhgKO0XL",
        "url": "https://www.instagram.com/p/Da2ONBxIMjN/",
        "account": "@funlab_official",
        "content_type": "image",
    },
    {
        "brand": "FUNLAB",
        "platform": "Facebook",
        "record_id": "recvpwuhI8CpWL",
        "url": "https://www.facebook.com/photo.php?fbid=887260437775419&set=a.116671061501031&type=3",
        "account": "FUNLAB FB Page",
        "content_type": "page_photo",
    },
]


class SocialCrmSyncError(RuntimeError):
    pass


def local_now() -> datetime:
    return datetime.now().astimezone()


def format_datetime(value: str | datetime | None = None) -> str:
    if value is None:
        return local_now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return local_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text.replace("T", " ")[:19]


def week_key(today=None) -> str:
    d = today or local_now().date()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def compact_error(value: Any, limit: int = 260) -> str:
    raw = str(value or "").replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"access_token=[^&\s]+", "access_token=[redacted]", raw, flags=re.I)
    raw = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", raw, flags=re.I)
    raw = re.sub(r'"access_token"\s*:\s*"[^"]+"', '"access_token":"[redacted]"', raw, flags=re.I)
    raw = re.sub(r'"refresh_token"\s*:\s*"[^"]+"', '"refresh_token":"[redacted]"', raw, flags=re.I)
    raw = re.sub(r'"client_secret"\s*:\s*"[^"]+"', '"client_secret":"[redacted]"', raw, flags=re.I)
    return raw[:limit]


def load_env_json(raw: str, label: str) -> dict[str, Any]:
    if not raw.strip():
        raise SocialCrmSyncError(f"{label} is not configured")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SocialCrmSyncError(f"{label} is not valid JSON") from exc


def part_timeout_seconds() -> float:
    raw = os.getenv("SOCIAL_CRM_P0_PART_TIMEOUT_SECONDS", "70")
    try:
        value = float(raw)
    except ValueError:
        value = 70.0
    return max(10.0, min(value, 120.0))


async def request_json(
    method: str,
    url: str,
    *,
    bearer: str | None = None,
    basic: tuple[str, str] | None = None,
    form: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 45,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if basic:
        encoded = base64.b64encode(f"{basic[0]}:{basic[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    if form is not None:
        data = {k: str(v) for k, v in form.items()}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, params=params, data=data, json=json_body, headers=headers)
    try:
        payload = resp.json() if resp.content else {}
    except json.JSONDecodeError:
        payload = {"message": resp.text[:500]}
    if resp.status_code >= 400:
        payload.setdefault("ok", False)
        payload.setdefault("http_status", resp.status_code)
    return payload


async def request_json_with_retry(url: str, *, attempts: int = 3, sleep_seconds: float = 2.0, **kwargs) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        last = await request_json("GET", url, **kwargs)
        status = safe_int(last.get("http_status") or last.get("status"))
        message = str(last.get("message") or last.get("detail") or "")
        if last.get("ok") is not False and status is None:
            return last
        if status not in (429, 500, 502, 503, 504) and "Service Unavailable" not in message:
            return last
        if attempt < attempts:
            await sleep(sleep_seconds * attempt)
    return last


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def metric_value(meta_data: Any, name: str) -> int | None:
    if not isinstance(meta_data, dict):
        return None
    metrics = meta_data.get("data")
    if isinstance(metrics, dict):
        metrics = metrics.get("data")
    if not isinstance(metrics, list):
        return None
    for metric in metrics:
        if metric.get("name") != name:
            continue
        values = metric.get("values") or []
        if not values:
            return None
        return safe_int(values[-1].get("value"))
    return None


async def meta_sync(settings: Settings, window: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    checked_at = format_datetime()
    rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "checked_at": checked_at,
        "source": "Meta live read via existing service endpoint",
        "service": settings.social_crm_meta_service_url,
        "records": [],
        "safe_output": True,
    }

    health = await request_json("GET", f"{settings.social_crm_meta_service_url.rstrip('/')}/health", timeout=45)
    evidence["health"] = {
        "ok": health.get("ok"),
        "meta_configured": health.get("meta_configured"),
        "commit_enabled": health.get("commit_enabled"),
        "asset_prepare_enabled": health.get("asset_prepare_enabled"),
    }
    if not settings.service_token:
        for item in META_RECORDS:
            error = "远程服务鉴权变量未设置；需在执行环境补齐后再同步"
            rows.append(meta_row(item, checked_at, {}, False, "blocker", error))
        return rows, build_platform_summaries(rows, "cloud:social-crm-p0"), evidence

    for item in META_RECORDS:
        response = await request_json(
            "POST",
            f"{settings.social_crm_meta_service_url.rstrip('/')}/insights/poll",
            bearer=settings.service_token,
            json_body={"record_id": item["record_id"], "window": window},
            timeout=90,
        )
        ok = bool(response.get("ok"))
        meta_data = response.get("data")
        metrics = {
            "reach": metric_value(meta_data, "reach"),
            "likes": metric_value(meta_data, "likes"),
            "comments": metric_value(meta_data, "comments"),
            "shares": metric_value(meta_data, "shares"),
            "saved": metric_value(meta_data, "saved"),
            "total_interactions": metric_value(meta_data, "total_interactions"),
        }
        if ok and any(v is not None for v in metrics.values()):
            sync_status = "已同步"
            error = f"无；{response.get('status') or 'insights-fetched'}"
        elif ok:
            sync_status = "只读样本"
            error = "API 读成功，但当前样本未返回可用基础指标；需补稳定指标口径"
        else:
            sync_status = "blocker"
            error = compact_error(response.get("message") or response.get("detail") or response)

        evidence["records"].append(
            {
                "brand": item["brand"],
                "platform": item["platform"],
                "record_id": item["record_id"],
                "url": item["url"],
                "ok": ok,
                "status": response.get("status"),
                "metrics": metrics,
                "error": None if ok else error,
            }
        )
        rows.append(meta_row(item, checked_at, metrics, ok, sync_status, error))
    return rows, build_platform_summaries(rows, "cloud:social-crm-p0"), evidence


def meta_row(item: dict[str, Any], checked_at: str, metrics: dict[str, int | None], ok: bool, sync_status: str, error: str) -> dict[str, Any]:
    return {
        "同步键": f"{item['brand']}|{item['platform']}|{item['record_id']}|insights",
        "品牌": item["brand"],
        "平台": item["platform"],
        "账号": item["account"],
        "平台内容ID": item["record_id"],
        "内容链接": item["url"],
        "发布时间": checked_at,
        "内容类型": item["content_type"],
        "同步状态": sync_status,
        "同步来源": "API live read",
        "证据路径": "cloud:social-crm-p0",
        "安全备注": SAFE_NOTE,
        "reach": metrics.get("reach"),
        "likes": metrics.get("likes"),
        "comments": metrics.get("comments"),
        "shares": metrics.get("shares"),
        "saved": metrics.get("saved"),
        "total_interactions": metrics.get("total_interactions"),
        "错误原因": error if not ok or error else "无",
    }


async def youtube_access_token(token_json: dict[str, Any]) -> str:
    refresh_token = token_json.get("refresh_token")
    client_id = token_json.get("client_id")
    client_secret = token_json.get("client_secret")
    token_uri = token_json.get("token_uri") or "https://oauth2.googleapis.com/token"
    if not (refresh_token and client_id and client_secret):
        raise SocialCrmSyncError("YouTube OAuth token JSON is missing refresh_token/client_id/client_secret")
    refreshed = await request_json(
        "POST",
        token_uri,
        form={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=45,
    )
    if refreshed.get("ok") is False or not refreshed.get("access_token"):
        raise SocialCrmSyncError("YouTube token refresh failed: " + compact_error(refreshed))
    return str(refreshed["access_token"])


async def youtube_sync(settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    checked_at = format_datetime()
    accounts = [
        {
            "brand": "FUNLAB",
            "account": "@funlabswitch",
            "expected_title": "FUNLAB",
            "token_json": settings.social_crm_youtube_token_funlab_json,
        },
        {
            "brand": "POWKONG",
            "account": "@POWKONG",
            "expected_title": "POWKONG",
            "token_json": settings.social_crm_youtube_token_powkong_json,
        },
    ]
    evidence: dict[str, Any] = {"checked_at": checked_at, "source": "YouTube API live read", "accounts": [], "safe_output": True}
    rows: list[dict[str, Any]] = []
    for account in accounts:
        try:
            token_json = load_env_json(account["token_json"], f"youtube token {account['brand']}")
            access = await youtube_access_token(token_json)
            result = await read_youtube_account(access, account)
            evidence["accounts"].append(result)
            videos = result.get("latest_videos") or []
            if videos:
                for video in videos:
                    rows.append(youtube_video_row(account, video))
            else:
                rows.append(youtube_status_row(account, checked_at, "已同步", "账号只读成功；最近未返回视频"))
        except Exception as exc:
            error = "YouTube 只读同步失败：" + compact_error(exc)
            evidence["accounts"].append({"brand": account["brand"], "ok": False, "error": error})
            rows.append(youtube_status_row(account, checked_at, "blocker", error))
    return rows, build_platform_summaries(rows, "cloud:social-crm-p0"), evidence


async def read_youtube_account(access_token: str, account: dict[str, Any]) -> dict[str, Any]:
    channel_resp = await request_json(
        "GET",
        "https://www.googleapis.com/youtube/v3/channels",
        bearer=access_token,
        params={"part": "snippet,statistics,contentDetails", "mine": "true"},
        timeout=60,
    )
    if channel_resp.get("ok") is False:
        raise SocialCrmSyncError("YouTube channel read failed: " + compact_error(channel_resp))
    channels = channel_resp.get("items") or []
    if not channels:
        raise SocialCrmSyncError("授权账号下没有可读频道")
    channel = select_youtube_channel(channels, account["expected_title"])
    uploads_playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    playlist_resp = await request_json(
        "GET",
        "https://www.googleapis.com/youtube/v3/playlistItems",
        bearer=access_token,
        params={"part": "snippet,contentDetails", "playlistId": uploads_playlist, "maxResults": 5},
        timeout=60,
    )
    if playlist_resp.get("ok") is False:
        raise SocialCrmSyncError("YouTube playlist read failed: " + compact_error(playlist_resp))
    video_ids = [
        item.get("contentDetails", {}).get("videoId")
        for item in playlist_resp.get("items", [])
        if item.get("contentDetails", {}).get("videoId")
    ]
    latest_videos: list[dict[str, Any]] = []
    if video_ids:
        videos_resp = await request_json(
            "GET",
            "https://www.googleapis.com/youtube/v3/videos",
            bearer=access_token,
            params={"part": "snippet,statistics,status,contentDetails", "id": ",".join(video_ids)},
            timeout=60,
        )
        if videos_resp.get("ok") is False:
            raise SocialCrmSyncError("YouTube videos read failed: " + compact_error(videos_resp))
        for item in videos_resp.get("items", []):
            stats = item.get("statistics", {})
            status = item.get("status", {})
            snippet = item.get("snippet", {})
            latest_videos.append(
                {
                    "id": item.get("id"),
                    "title": snippet.get("title"),
                    "published_at": snippet.get("publishedAt"),
                    "privacy_status": status.get("privacyStatus"),
                    "upload_status": status.get("uploadStatus"),
                    "duration": item.get("contentDetails", {}).get("duration"),
                    "view_count": safe_int(stats.get("viewCount")),
                    "like_count": safe_int(stats.get("likeCount")),
                    "comment_count": safe_int(stats.get("commentCount")),
                }
            )
    analytics = await read_youtube_analytics(access_token)
    return {
        "brand": account["brand"],
        "ok": True,
        "channel": summarize_youtube_channel(channel),
        "latest_videos": latest_videos,
        "analytics_28d": analytics,
    }


def select_youtube_channel(channels: list[dict[str, Any]], expected_title: str) -> dict[str, Any]:
    expected = expected_title.strip().lower()
    for channel in channels:
        title = (channel.get("snippet", {}).get("title") or "").strip().lower()
        if title == expected:
            return channel
    titles = [item.get("snippet", {}).get("title") for item in channels]
    raise SocialCrmSyncError(f"授权频道不匹配：期望 {expected_title}，实际 {titles}")


def summarize_youtube_channel(channel: dict[str, Any]) -> dict[str, Any]:
    stats = channel.get("statistics", {})
    snippet = channel.get("snippet", {})
    return {
        "id": channel.get("id"),
        "title": snippet.get("title"),
        "custom_url": snippet.get("customUrl"),
        "subscriber_count": safe_int(stats.get("subscriberCount")),
        "video_count": safe_int(stats.get("videoCount")),
        "view_count": safe_int(stats.get("viewCount")),
    }


async def read_youtube_analytics(access_token: str) -> dict[str, Any]:
    today = local_now().date()
    start = today - timedelta(days=28)
    resp = await request_json(
        "GET",
        "https://youtubeanalytics.googleapis.com/v2/reports",
        bearer=access_token,
        params={
            "ids": "channel==MINE",
            "startDate": start.isoformat(),
            "endDate": today.isoformat(),
            "metrics": "views,estimatedMinutesWatched,averageViewDuration",
            "dimensions": "day",
            "sort": "day",
        },
        timeout=60,
    )
    if resp.get("ok") is False:
        return {"ok": False, "error": compact_error(resp)}
    return {
        "ok": True,
        "row_count": len(resp.get("rows", [])),
        "first_row": resp.get("rows", [None])[0],
        "last_row": resp.get("rows", [None])[-1] if resp.get("rows") else None,
    }


def youtube_video_row(account: dict[str, Any], video: dict[str, Any]) -> dict[str, Any]:
    likes = video.get("like_count")
    comments = video.get("comment_count")
    total = sum(v or 0 for v in (likes, comments))
    status = video.get("privacy_status") or "unknown"
    upload_status = video.get("upload_status") or "unknown"
    content_type = "private_upload" if status == "private" else ("shorts" if is_short_duration(video.get("duration")) else "video")
    return {
        "同步键": f"{account['brand']}|YouTube|{video['id']}|daily-read",
        "品牌": account["brand"],
        "平台": "YouTube",
        "账号": account["account"],
        "平台内容ID": video["id"],
        "内容链接": f"https://www.youtube.com/watch?v={video['id']}",
        "发布时间": format_datetime(video.get("published_at")),
        "内容类型": content_type,
        "同步状态": "已同步",
        "同步来源": "API live read",
        "证据路径": "cloud:social-crm-p0",
        "安全备注": SAFE_NOTE,
        "reach": video.get("view_count"),
        "likes": likes,
        "comments": comments,
        "total_interactions": total,
        "错误原因": f"无；privacyStatus={status}，uploadStatus={upload_status}",
    }


def youtube_status_row(account: dict[str, Any], checked_at: str, sync_status: str, message: str) -> dict[str, Any]:
    return {
        "同步键": f"{account['brand']}|YouTube|channel-daily|latest",
        "品牌": account["brand"],
        "平台": "YouTube",
        "账号": account["account"],
        "平台内容ID": "channel status",
        "内容链接": "",
        "发布时间": checked_at,
        "内容类型": "status_only",
        "同步状态": sync_status,
        "同步来源": "API live read",
        "证据路径": "cloud:social-crm-p0",
        "安全备注": SAFE_NOTE,
        "reach": 0,
        "likes": 0,
        "comments": 0,
        "total_interactions": 0,
        "错误原因": message,
    }


def is_short_duration(duration: str | None) -> bool:
    if not duration:
        return False
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return False
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds <= 60


def read_persisted_x_tokens(settings: Settings) -> dict[str, Any]:
    path = Path(settings.social_crm_x_token_persist_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def persist_x_tokens(settings: Settings, tokens: dict[str, Any]) -> None:
    path = Path(settings.social_crm_x_token_persist_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tokens, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


async def x_sync(settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    checked_at = format_datetime()
    persisted = read_persisted_x_tokens(settings)
    accounts = [
        {
            "brand": "FUNLAB",
            "expected_username": "Funlab_switch",
            "client_json": settings.social_crm_x_client_funlab_json,
            "token_json": persisted.get("FUNLAB") or settings.social_crm_x_token_funlab_json,
        },
        {
            "brand": "POWKONG",
            "expected_username": "POWKONGkong",
            "client_json": settings.social_crm_x_client_powkong_json,
            "token_json": persisted.get("POWKONG") or settings.social_crm_x_token_powkong_json,
        },
    ]
    evidence: dict[str, Any] = {"checked_at": checked_at, "source": "X API live read", "accounts": [], "safe_output": True}
    rows: list[dict[str, Any]] = []
    changed_tokens: dict[str, Any] = {}
    for account in accounts:
        try:
            result, credential = await read_x_account(account)
            changed_tokens[account["brand"]] = json.dumps(credential, ensure_ascii=False, separators=(",", ":"))
            evidence["accounts"].append(result)
            tweets = result.get("latest_posts") or []
            rows.append(
                x_status_row(
                    account,
                    result.get("account", {}),
                    checked_at,
                    "已同步",
                    f"账号身份与最近内容已回读；latest_count={len(tweets)}",
                )
            )
            for tweet in tweets:
                rows.append(x_post_row(account, result["account"], tweet))
        except Exception as exc:
            error = "X 只读同步失败：" + compact_error(exc)
            evidence["accounts"].append({"brand": account["brand"], "ok": False, "error": error})
            rows.append(x_status_row(account, {}, checked_at, "blocker", error))
    if changed_tokens:
        merged = {**persisted, **changed_tokens}
        persist_x_tokens(settings, merged)
    evidence["token_persistence"] = {
        "mode": "runtime_file",
        "persist_path_configured": bool(settings.social_crm_x_token_persist_path),
        "updated_brands": sorted(changed_tokens.keys()),
    }
    return rows, build_platform_summaries(rows, "cloud:social-crm-p0"), evidence


async def read_x_account(account: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    access, credential = await refresh_x_access(account)
    me = await request_json("GET", X_ME_URL, bearer=access, timeout=45)
    data = me.get("data")
    if not isinstance(data, dict) or not data.get("id"):
        raise SocialCrmSyncError("账号身份接口未返回有效用户")
    expected = account["expected_username"].lower()
    actual = (data.get("username") or "").lower()
    if expected and actual != expected:
        raise SocialCrmSyncError(f"账号不匹配：期望 @{account['expected_username']}，实际 @{data.get('username')}")
    tweets = await request_json_with_retry(
        f"https://api.x.com/2/users/{data['id']}/tweets",
        bearer=access,
        params={
            "max_results": "5",
            "tweet.fields": "created_at,public_metrics",
            "exclude": "retweets,replies",
        },
        timeout=45,
    )
    if tweets.get("ok") is False:
        raise SocialCrmSyncError("账号身份可读，但最近内容接口失败：" + compact_error(tweets.get("message") or tweets))
    latest_posts = []
    for tweet in tweets.get("data") or []:
        metrics = tweet.get("public_metrics") or {}
        latest_posts.append(
            {
                "id": tweet.get("id"),
                "created_at": tweet.get("created_at"),
                "text_length": len(tweet.get("text") or ""),
                "public_metrics": {
                    "retweet_count": safe_int(metrics.get("retweet_count")),
                    "reply_count": safe_int(metrics.get("reply_count")),
                    "like_count": safe_int(metrics.get("like_count")),
                    "quote_count": safe_int(metrics.get("quote_count")),
                    "bookmark_count": safe_int(metrics.get("bookmark_count")),
                },
            }
        )
    return (
        {
            "brand": account["brand"],
            "ok": True,
            "account": {
                "id": data.get("id"),
                "username": data.get("username"),
                "name": data.get("name"),
                "public_metrics": data.get("public_metrics"),
            },
            "latest_posts": latest_posts,
            "result_count": len(latest_posts),
        },
        credential,
    )


async def refresh_x_access(account: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    client = load_env_json(account["client_json"], f"x client {account['brand']}")
    credential = load_env_json(account["token_json"], f"x token {account['brand']}")
    refresh_value = credential.get("refresh_token")
    client_id = client.get("client_id")
    client_secret = client.get("client_secret")
    if not refresh_value or not client_id or not client_secret:
        raise SocialCrmSyncError("X OAuth JSON is missing refresh_token/client_id/client_secret")
    refreshed = await request_json(
        "POST",
        X_TOKEN_URL,
        basic=(client_id, client_secret),
        form={"grant_type": "refresh_token", "refresh_token": refresh_value},
        timeout=45,
    )
    if refreshed.get("ok") is False or not refreshed.get("access_token"):
        raise SocialCrmSyncError("刷新授权失败：" + compact_error(refreshed.get("message") or refreshed))
    for source_key, target_key in (
        ("access_token", "access_token"),
        ("refresh_token", "refresh_token"),
        ("token_type", "token_type"),
        ("expires_in", "expires_in"),
        ("scope", "scope_granted"),
    ):
        if source_key in refreshed:
            credential[target_key] = refreshed[source_key]
    credential["token_refreshed_at"] = int(time.time())
    return str(credential["access_token"]), credential


def x_post_row(account: dict[str, Any], x_account: dict[str, Any], tweet: dict[str, Any]) -> dict[str, Any]:
    metrics = tweet.get("public_metrics") or {}
    likes = metrics.get("like_count") or 0
    comments = metrics.get("reply_count") or 0
    shares = (metrics.get("retweet_count") or 0) + (metrics.get("quote_count") or 0)
    saved = metrics.get("bookmark_count")
    total = likes + comments + shares + (saved or 0)
    username = x_account.get("username") or account["expected_username"]
    return {
        "同步键": f"{account['brand']}|X|{tweet['id']}|daily-read",
        "品牌": account["brand"],
        "平台": "X",
        "账号": f"@{username}",
        "平台内容ID": tweet["id"],
        "内容链接": f"https://x.com/{username}/status/{tweet['id']}",
        "发布时间": format_datetime(tweet.get("created_at")),
        "内容类型": "text",
        "同步状态": "已同步",
        "同步来源": "API live read",
        "证据路径": "cloud:social-crm-p0",
        "安全备注": SAFE_NOTE,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saved": saved,
        "total_interactions": total,
        "错误原因": "无；账号身份与最近内容已回读",
    }


def x_status_row(account: dict[str, Any], x_account: dict[str, Any], checked_at: str, status: str, message: str) -> dict[str, Any]:
    username = x_account.get("username") or account["expected_username"]
    return {
        "同步键": f"{account['brand']}|X|account-daily|latest",
        "品牌": account["brand"],
        "平台": "X",
        "账号": f"@{username}",
        "平台内容ID": "account status",
        "内容链接": "",
        "发布时间": checked_at,
        "内容类型": "status_only",
        "同步状态": status,
        "同步来源": "API live read",
        "证据路径": "cloud:social-crm-p0",
        "安全备注": SAFE_NOTE,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "saved": 0,
        "total_interactions": 0,
        "错误原因": message,
    }


def build_platform_summaries(rows: list[dict[str, Any]], evidence_path: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["品牌"], row["平台"]), []).append(row)
    summaries = []
    current_week = week_key()
    checked_at = format_datetime()
    for (brand, platform), items in sorted(grouped.items()):
        blockers = [item.get("错误原因") for item in items if item.get("同步状态") == "blocker"]
        healthy_items = [item for item in items if item.get("同步状态") in ("已同步", "只读样本")]
        interactions = sum((item.get("total_interactions") or 0) for item in healthy_items)
        count = sum(1 for item in healthy_items if item.get("内容类型") != "status_only")
        health = "阻塞" if blockers and not healthy_items else "健康"
        if platform == "X" and any(item.get("同步状态") == "blocker" for item in items):
            health = "待确认" if healthy_items else "阻塞"
        gap, next_action = platform_gap_next(platform)
        summaries.append(
            {
                "快照键": f"{current_week}|{brand}|{platform}",
                "品牌": brand,
                "平台": platform,
                "周期": current_week,
                "生成时间": checked_at,
                "账号健康": health,
                "最近发帖数": count,
                "基础互动": interactions,
                "权限缺口": "; ".join(blockers[:2]) if blockers else gap,
                "当前blocker": "; ".join(blockers[:2]) if blockers else "无",
                "下一步动作": next_action,
                "证据范围": evidence_path,
                "安全备注": "只汇总状态、缺口、指标和下一步；敏感凭据不在此表保存。",
            }
        )
    return summaries


def platform_gap_next(platform: str) -> tuple[str, str]:
    if platform in ("Instagram", "Facebook"):
        return ("P0 只读已接；真实发布仍延后到 P1 人审 gate", "保持每日只读；第 2 周补 Page/IG 最近内容列表")
    if platform == "YouTube":
        return ("P0 不做公开发布；公开发布需另做平台审核判断", "继续同步最近视频；analytics 异常时单独登记 blocker")
    if platform == "X":
        return ("真实发帖不进 P0；指标受 X 套餐和 scope 限制", "继续只读最近内容；任何发布动作必须走人审")
    return ("未纳入本次每日同步", "等待 P0 范围外平台审核或账号确认")


class FeishuBaseV3:
    def __init__(self, app_id: str, app_secret: str, base_token: str):
        if not (app_id and app_secret and base_token):
            raise SocialCrmSyncError("Feishu Base credentials are not configured")
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_token = base_token
        self._token: str | None = None
        self._base = "https://open.feishu.cn/open-apis"

    async def token(self) -> str:
        if self._token:
            return self._token
        data = await request_json(
            "POST",
            f"{self._base}/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        if data.get("code") != 0:
            raise SocialCrmSyncError(f"tenant token error: {data.get('code')} {data.get('msg')}")
        self._token = data["tenant_access_token"]
        return self._token

    async def request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        token = await self.token()
        timeout = httpx.Timeout(12.0, connect=5.0, read=12.0, write=12.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
        except httpx.TimeoutException as exc:
            raise SocialCrmSyncError(f"Feishu Base API timeout: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise SocialCrmSyncError(f"Feishu Base API request failed: {method} {path}: {compact_error(exc)}") from exc
        try:
            data = resp.json() if resp.content else {}
        except json.JSONDecodeError:
            data = {"msg": resp.text[:500]}
        if resp.status_code >= 400 or data.get("code") not in (0, None):
            raise SocialCrmSyncError(f"Feishu Base API error: HTTP {resp.status_code}, code={data.get('code')}, msg={data.get('msg')}")
        return data

    async def list_records(self, table_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        limit = 200
        while True:
            params = {"limit": limit, "offset": offset}
            data = await self.request("GET", f"/base/v3/bases/{self.base_token}/tables/{table_id}/records", params=params)
            body = data.get("data", {})
            page_items = parse_base_v3_records_page(body)
            items.extend(page_items)
            if not body.get("has_more"):
                return items
            if not page_items:
                return items
            offset += len(page_items)

    async def create_record(self, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = await self.request(
            "POST",
            f"/base/v3/bases/{self.base_token}/tables/{table_id}/records",
            json=fields,
        )
        return data.get("data", {})

    async def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        data = await self.request(
            "PATCH",
            f"/base/v3/bases/{self.base_token}/tables/{table_id}/records/{record_id}",
            json=fields,
        )
        return data.get("data", {})


def parse_base_v3_records_page(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if isinstance(items, list):
        return items

    fields = body.get("fields") or []
    rows = body.get("data") or []
    record_ids = body.get("record_id_list") or []
    parsed: list[dict[str, Any]] = []
    for record_id, row in zip(record_ids, rows):
        row_fields: dict[str, Any] = {}
        if isinstance(row, dict):
            row_fields = row
        elif isinstance(row, list):
            for field_name, value in zip(fields, row):
                row_fields[str(field_name)] = value
        parsed.append({"record_id": record_id, "fields": row_fields})
    return parsed


def clean_row_for_base(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}


async def upsert_rows(client: FeishuBaseV3, table_id: str, key_field: str, rows: list[dict[str, Any]], commit: bool) -> dict[str, int]:
    counts = {"created": 0, "updated": 0, "planned": len(rows)}
    if not commit or not rows:
        return counts
    mapping: dict[str, str] = {}
    for record in await client.list_records(table_id):
        fields = record.get("fields", {})
        key = fields.get(key_field)
        if isinstance(key, str) and key:
            mapping[key] = record.get("record_id", "")
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for row in rows:
        key = row.get(key_field)
        if not key:
            raise SocialCrmSyncError(f"待写记录缺少幂等字段：{key_field}")
        fields = clean_row_for_base(row)
        existing_id = mapping.get(str(key))
        if existing_id:
            updates.append({"record_id": existing_id, "fields": fields})
        else:
            creates.append({"fields": fields})
    for item in updates:
        await client.update_record(table_id, item["record_id"], item["fields"])
        counts["updated"] += 1
    for item in creates:
        await client.create_record(table_id, item["fields"])
        counts["created"] += 1
    return counts


async def run_social_crm_p0_sync(req: SocialCrmP0SyncRequest, settings: Settings) -> dict[str, Any]:
    parts: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]] = []
    errors: list[str] = []
    jobs = [
        (name, func)
        for name, skip, func in (
        ("meta", req.skip_meta, lambda: meta_sync(settings, req.window)),
        ("youtube", req.skip_youtube, lambda: youtube_sync(settings)),
        ("x", req.skip_x, lambda: x_sync(settings)),
        )
        if not skip
    ]
    if jobs:
        results = await asyncio.gather(*(run_part_with_timeout(name, func) for name, func in jobs))
        for name, rows, summaries, evidence, error in results:
            parts.append((name, rows, summaries, evidence))
            if error:
                errors.append(error)

    rows: list[dict[str, Any]] = []
    summaries_by_key: dict[str, dict[str, Any]] = {}
    evidence = {"checked_at": format_datetime(), "parts": {}, "safe_output": True, "errors": errors}
    for name, part_rows, part_summaries, part_evidence in parts:
        rows.extend(part_rows)
        evidence["parts"][name] = part_evidence
        for summary in part_summaries:
            summaries_by_key[summary["快照键"]] = summary
    summaries = list(summaries_by_key.values())

    requested_commit = bool(req.commit)
    effective_commit = requested_commit and settings.social_crm_p0_write_enabled
    if requested_commit and not settings.social_crm_p0_write_enabled:
        errors.append("base-write: SOCIAL_CRM_P0_WRITE_ENABLED=false，已只读运行但未写 Base")

    post_counts = {"created": 0, "updated": 0, "planned": len(rows)}
    snapshot_counts = {"created": 0, "updated": 0, "planned": len(summaries)}
    if effective_commit:
        client = FeishuBaseV3(
            settings.feishu_bitable_app_id,
            settings.feishu_bitable_app_secret,
            settings.social_crm_p0_base_token,
        )
        try:
            post_counts = await upsert_rows(client, settings.social_crm_p0_post_table_id, "同步键", rows, True)
            snapshot_counts = await upsert_rows(client, settings.social_crm_p0_snapshot_table_id, "快照键", summaries, True)
        except Exception as exc:
            errors.append(f"base-write: {compact_error(exc)}")

    return {
        "ok": not errors,
        "status": "base-updated" if effective_commit else ("write-blocked" if requested_commit else "dry-run"),
        "commit_requested": requested_commit,
        "commit_effective": effective_commit,
        "write_gate_enabled": settings.social_crm_p0_write_enabled,
        "post_rows": post_counts,
        "snapshot_rows": snapshot_counts,
        "platform_parts": [name for name, *_ in parts],
        "errors": errors,
        "evidence": evidence,
    }


async def run_part_with_timeout(
    name: str,
    func: Any,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str | None]:
    timeout = part_timeout_seconds()
    try:
        rows, summaries, evidence = await asyncio.wait_for(func(), timeout=timeout)
        return name, rows, summaries, evidence, None
    except asyncio.TimeoutError:
        error = f"{name}: 单平台只读同步超过 {timeout:g}s，已写入 blocker，避免整次 daily 任务卡死"
        rows, summaries, evidence = fallback_platform_part(name, error)
        return name, rows, summaries, evidence, error
    except Exception as exc:
        error = f"{name}: {compact_error(exc)}"
        rows, summaries, evidence = fallback_platform_part(name, error)
        return name, rows, summaries, evidence, error


def fallback_platform_part(
    name: str,
    message: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    checked_at = format_datetime()
    rows: list[dict[str, Any]] = []
    if name == "meta":
        for item in META_RECORDS:
            rows.append(meta_row(item, checked_at, {}, False, "blocker", message))
    elif name == "youtube":
        for account in (
            {"brand": "FUNLAB", "account": "@funlabswitch"},
            {"brand": "POWKONG", "account": "@POWKONG"},
        ):
            rows.append(youtube_status_row(account, checked_at, "blocker", message))
    elif name == "x":
        for account in (
            {"brand": "FUNLAB", "expected_username": "Funlab_switch"},
            {"brand": "POWKONG", "expected_username": "POWKONGkong"},
        ):
            rows.append(x_status_row(account, {}, checked_at, "blocker", message))

    evidence = {
        "checked_at": checked_at,
        "source": f"{name} fallback blocker",
        "safe_output": True,
        "error": message,
    }
    return rows, build_platform_summaries(rows, "cloud:social-crm-p0"), evidence
