from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from .rules import multi_value, number_value, select_value, text_value


DISCOVERY_VERSION = "fb-ig-discovery-v1.0"
DEFAULT_WEEKLY_BUSINESS_FOCUS = "新品/主推产品场景曝光；优先保证产品保真、品牌调性和可复用参考对象沉淀。"
KOL_VISUAL_GATE_NOTE = (
    "只筛可复用画面的 IG/FB 静态图片帖；账号主页、YouTube、Reels/视频页、普通网站链接和占位链接都不能作为图片参考。"
)

DEFAULT_ACCOUNT_SLOTS = [
    {"账号名称": "FUNLAB FB", "品牌": "FUNLAB", "平台": "Facebook", "每周候选数": 3},
    {"账号名称": "FUNLAB IG", "品牌": "FUNLAB", "平台": "Instagram", "每周候选数": 3},
    {"账号名称": "POWKONG FB", "品牌": "Powkong", "平台": "Facebook", "每周候选数": 3},
    {"账号名称": "POWKONG IG", "品牌": "Powkong", "平台": "Instagram", "每周候选数": 3},
]

REFERENCE_SEEDS = {
    "FUNLAB": [
        {
            "title": "8BitDo gaming setup reference",
            "url": "https://www.instagram.com/8bitdo/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "产品场景",
            "borrow": "桌面/客厅游戏场景、手持构图、低亮度氛围、控制器特写节奏",
            "avoid": "竞品 logo、竞品产品外观、屏幕 UI、联名 IP 标识",
            "tags": "controller, gaming setup, handheld, lifestyle",
            "reason": "FUNLAB 需要参考成熟手柄品牌的场景化表达，但只借鉴光线、构图和使用情境。",
            "score": 86,
        },
        {
            "title": "GameSir controller lifestyle reference",
            "url": "https://www.instagram.com/gamesir/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "卖点教育",
            "borrow": "功能卖点画面拆解、按键细节展示、手柄与屏幕的前后景关系",
            "avoid": "竞品卖点文案、产品 ID、包装设计、电竞联名元素",
            "tags": "controller, product detail, gamer desk",
            "reason": "适合反查手柄卖点图如何把功能讲清楚，再替换为 FUNLAB 产品与品牌语言。",
            "score": 82,
        },
        {
            "title": "GuliKit feature demo reference",
            "url": "https://www.instagram.com/gulikit/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "卖点教育",
            "borrow": "霍尔摇杆、低延迟、续航等功能型内容的解释结构",
            "avoid": "竞品参数宣称、logo、外观比例、专利/认证表述",
            "tags": "hall effect, controller feature, product demo",
            "reason": "FUNLAB 手柄内容常需要把技术点讲自然，可借鉴功能表达结构。",
            "score": 78,
        },
    ],
    "POWKONG": [
        {
            "title": "JSAUX accessory scene reference",
            "url": "https://www.instagram.com/jsaux_official/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "产品场景",
            "borrow": "掌机/主机配件桌面场景、收纳与便携画面、前后景层次",
            "avoid": "竞品 logo、竞品产品结构、Steam Deck/IP 授权视觉",
            "tags": "gaming accessory, dock, desk setup, portable",
            "reason": "POWKONG 可借鉴配件类产品的使用场景组织方式，保持产品结构不复制。",
            "score": 84,
        },
        {
            "title": "Skull & Co accessory lifestyle reference",
            "url": "https://www.instagram.com/skullnco/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "产品场景",
            "borrow": "便携场景、收纳包/保护配件的外出使用构图、生活化道具",
            "avoid": "竞品外观、logo、包装、联名图案",
            "tags": "case, travel, switch accessory, lifestyle",
            "reason": "POWKONG 包类/配件类内容适合参考其场景化表达。",
            "score": 80,
        },
        {
            "title": "dbrand product detail reference",
            "url": "https://www.instagram.com/dbrand/",
            "type": "竞品账号",
            "platform": "Instagram",
            "pillar": "品牌幕后",
            "borrow": "产品纹理、材质、边缘细节和冷幽默视觉节奏",
            "avoid": "品牌语气复制、竞品图案、竞品 SKU、嘲讽式文案",
            "tags": "texture, product detail, accessory, brand tone",
            "reason": "适合借鉴材质细节拍法，但文案语气必须改为 POWKONG 自有风格。",
            "score": 76,
        },
    ],
}

KOL_SEEDS = {
    "FUNLAB": [
        {
            "handle": "Kevin Kenson",
            "url": "https://www.instagram.com/kevinkenson/",
            "platform": "Instagram",
            "followers": "gaming tech creator",
            "country": "US",
            "language": "EN",
            "style": "Switch accessories, controller reviews, desk/game setup visuals",
            "reason": "受众与 Switch 控制器高度匹配，适合 FUNLAB 手柄和隐藏式发光卖点。",
            "sample_1": "https://www.instagram.com/kevinkenson/",
            "sample_2": "https://www.youtube.com/@KevinKenson",
            "score": 88,
            "risk": "需避免直接复用其评测截图或未经授权头像；只作为账号参考。",
        },
        {
            "handle": "WULFF DEN",
            "url": "https://www.instagram.com/wulffden/",
            "platform": "Instagram",
            "followers": "gaming creator",
            "country": "US",
            "language": "EN",
            "style": "Nintendo/Switch accessories, casual review scenes, creator-led trust",
            "reason": "适合 FUNLAB 做场景图和功能解释的 KOL 参考，受众对 Switch 配件敏感。",
            "sample_1": "https://www.instagram.com/wulffden/",
            "sample_2": "https://www.youtube.com/@WulffDen",
            "score": 84,
            "risk": "涉及 Nintendo 生态时只能借鉴场景，不使用未授权 IP 角色。",
        },
        {
            "handle": "Spawn Wave",
            "url": "https://www.instagram.com/spawnwave/",
            "platform": "Instagram",
            "followers": "gaming news creator",
            "country": "US",
            "language": "EN",
            "style": "gaming hardware, news, controller/accessory discussion",
            "reason": "适合作为 FUNLAB 手柄类选题的趋势和硬件讨论参考。",
            "sample_1": "https://www.instagram.com/spawnwave/",
            "sample_2": "https://www.youtube.com/@SpawnWave",
            "score": 80,
            "risk": "账号内容偏新闻，需要转成产品场景而不是新闻复述。",
        },
        {
            "handle": "TechDweeb",
            "url": "https://www.instagram.com/techdweeb/",
            "platform": "Instagram",
            "followers": "retro/gaming tech creator",
            "country": "CA",
            "language": "EN",
            "style": "playful gaming hardware, retro handhelds, approachable setup visuals",
            "reason": "FUNLAB 可借鉴轻松、真实玩家视角，降低产品广告感。",
            "sample_1": "https://www.instagram.com/techdweeb/",
            "sample_2": "https://www.youtube.com/@TechDweeb",
            "score": 78,
            "risk": "风格较强，需控制文案不要过度模仿个人口吻。",
        },
        {
            "handle": "SwitchUp",
            "url": "https://www.instagram.com/switchupgaming/",
            "platform": "Instagram",
            "followers": "Switch-focused creator",
            "country": "UK",
            "language": "EN",
            "style": "Switch game/accessory audience, practical buyer guidance",
            "reason": "受众集中在 Switch 场景，适合 FUNLAB 手柄选题与 GEO 问题。",
            "sample_1": "https://www.instagram.com/switchupgaming/",
            "sample_2": "https://www.youtube.com/@SwitchUpYt",
            "score": 76,
            "risk": "优先参考选题结构，不引用未经授权评测结论。",
        },
    ],
    "POWKONG": [
        {
            "handle": "Retro Game Corps",
            "url": "https://www.instagram.com/retrogamecorps/",
            "platform": "Instagram",
            "followers": "retro handheld creator",
            "country": "US",
            "language": "EN",
            "style": "handheld accessories, practical setup, detail-heavy buyer guidance",
            "reason": "POWKONG 配件、底座和收纳类内容可借鉴其清晰实用的展示方式。",
            "sample_1": "https://www.instagram.com/retrogamecorps/",
            "sample_2": "https://www.youtube.com/@RetroGameCorps",
            "score": 88,
            "risk": "不要复用评测截图和具体结论，只借鉴内容结构。",
        },
        {
            "handle": "ETA PRIME",
            "url": "https://www.instagram.com/etaprime/",
            "platform": "Instagram",
            "followers": "gaming hardware creator",
            "country": "US",
            "language": "EN",
            "style": "hardware demo, gaming desk, practical accessory use cases",
            "reason": "适合 POWKONG 做配件兼容性、场景和功能教育选题。",
            "sample_1": "https://www.instagram.com/etaprime/",
            "sample_2": "https://www.youtube.com/@ETAPRIME",
            "score": 84,
            "risk": "技术表述需保持保守，不引用未经验证参数。",
        },
        {
            "handle": "Taki Udon",
            "url": "https://www.instagram.com/takiudon/",
            "platform": "Instagram",
            "followers": "handheld/gaming hardware creator",
            "country": "US",
            "language": "EN",
            "style": "handheld hardware, product detail, enthusiast community",
            "reason": "受众对掌机和配件很敏感，适合 POWKONG 配件参考对象池。",
            "sample_1": "https://www.instagram.com/takiudon/",
            "sample_2": "https://www.youtube.com/@TakiUdon",
            "score": 82,
            "risk": "社区技术要求高，避免夸大兼容性。",
        },
        {
            "handle": "Retro Handhelds",
            "url": "https://www.instagram.com/retrohandhelds/",
            "platform": "Instagram",
            "followers": "community media account",
            "country": "Global",
            "language": "EN",
            "style": "community gear shots, accessory discovery, handheld lifestyle",
            "reason": "适合 POWKONG 观察社区偏好的配件画面和话题趋势。",
            "sample_1": "https://www.instagram.com/retrohandhelds/",
            "sample_2": "https://retrohandhelds.gg/",
            "score": 78,
            "risk": "社区聚合内容需回到原作者授权，不能直接复用图片。",
        },
        {
            "handle": "The Phawx",
            "url": "https://www.instagram.com/thephawx/",
            "platform": "Instagram",
            "followers": "handheld gaming creator",
            "country": "US",
            "language": "EN",
            "style": "portable gaming hardware, performance-focused accessory context",
            "reason": "适合 POWKONG 掌机配件和底座类选题的场景参考。",
            "sample_1": "https://www.instagram.com/thephawx/",
            "sample_2": "https://www.youtube.com/@ThePhawx",
            "score": 76,
            "risk": "内容偏硬件深度，需要转成 FB/IG 易读图文。",
        },
    ],
}


def _fields(record_or_fields: dict[str, Any]) -> dict[str, Any]:
    if "fields" in record_or_fields and isinstance(record_or_fields.get("fields"), dict):
        return record_or_fields["fields"]
    return record_or_fields


def _record_id(record_or_fields: dict[str, Any]) -> str:
    return text_value(record_or_fields.get("record_id") or record_or_fields.get("id"))


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", text_value(value).lower())


def _url_host_path(raw: Any) -> tuple[str, str]:
    value = text_value(raw).strip()
    if not value:
        return "", ""
    parsed = urlparse(value)
    return parsed.netloc.lower(), parsed.path.lower().rstrip("/")


def _is_instagram_image_post_url(raw: Any) -> bool:
    host, path = _url_host_path(raw)
    if not host.endswith("instagram.com"):
        return False
    if path.startswith(("/reel/", "/reels/", "/tv/", "/stories/")):
        return False
    return path.startswith("/p/") and len([part for part in path.split("/") if part]) >= 2


def _is_facebook_image_post_url(raw: Any) -> bool:
    host, path = _url_host_path(raw)
    if not host.endswith("facebook.com") and host not in {"fb.com", "m.facebook.com"}:
        return False
    if any(blocked in path for blocked in ("/watch", "/videos", "/reel", "/reels", "/stories")):
        return False
    return (
        path.startswith("/photo")
        or path.startswith("/permalink.php")
        or "/photos/" in path
        or "/posts/" in path
    )


def is_visual_reference_sample_url(raw: Any) -> bool:
    return _is_instagram_image_post_url(raw) or _is_facebook_image_post_url(raw)


def _is_placeholder_visual_url(raw: Any) -> bool:
    value = text_value(raw).lower()
    if not value:
        return False
    markers = ("example.com", "weeklyvisual", "visual123", "noimage", "placeholder", "dummy", "/p/test")
    return any(marker in value for marker in markers)


def _kol_visual_sample_links(seed: dict[str, Any]) -> list[str]:
    links = [
        text_value(seed.get("sample_1")),
        text_value(seed.get("sample_2")),
        text_value(seed.get("sample_3")),
    ]
    return [link for link in links if is_visual_reference_sample_url(link) and not _is_placeholder_visual_url(link)]


def _first_text(mapping: dict[str, Any], *names: str) -> str:
    for name in names:
        value = text_value(mapping.get(name))
        if value:
            return value
    return ""


def _platform_from_url(raw: Any, fallback: Any = "") -> str:
    host, _ = _url_host_path(raw)
    if "instagram.com" in host:
        return "Instagram"
    if "facebook.com" in host or host in {"fb.com", "m.facebook.com"}:
        return "Facebook"
    return text_value(fallback) or "Instagram"


def _visual_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [text_value(item) for item in raw if text_value(item)]
    value = text_value(raw)
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,，;/；\n]+", value) if part.strip()]


def _visual_text(post: dict[str, Any]) -> str:
    parts = [
        text_value(post.get("visual_tags")),
        text_value(post.get("视觉标签")),
        text_value(post.get("scene_type")),
        text_value(post.get("场景类型")),
        text_value(post.get("borrow")),
        text_value(post.get("可借鉴元素")),
        text_value(post.get("reason")),
        text_value(post.get("入选原因")),
        text_value(post.get("caption")),
        text_value(post.get("文案")),
    ]
    return " ".join(part for part in parts if part).lower()


PEOPLE_FIRST_VISUAL_MARKERS = (
    "selfie",
    "portrait",
    "face-first",
    "people-first",
    "person-first",
    "person as main",
    "human subject",
    "creator as subject",
    "自拍",
    "人像",
    "脸部",
    "露脸",
    "人脸",
    "人物主体",
    "人物为主",
    "真人出镜",
    "博主本人",
    "博主出镜",
    "模特为主",
    "全身照",
)

PRODUCT_FIRST_VISUAL_MARKERS = (
    "hands only",
    "hand-only",
    "no face",
    "no human face",
    "product dominant",
    "product-first",
    "product hero",
    "product slot",
    "只露手",
    "手部特写",
    "不露脸",
    "无人物",
    "无人像",
    "产品主体",
    "产品为主",
    "产品占位",
    "产品特写",
    "静物",
    "桌搭",
)


def is_people_first_visual_post(post: dict[str, Any]) -> bool:
    text = " ".join(
        [
            _visual_text(post),
            text_value(post.get("risk")),
            text_value(post.get("风险备注")),
            text_value(post.get("avoid")),
            text_value(post.get("禁止复制元素")),
        ]
    ).lower()
    has_people_first_marker = any(term in text for term in PEOPLE_FIRST_VISUAL_MARKERS)
    has_product_first_marker = any(term in text for term in PRODUCT_FIRST_VISUAL_MARKERS)
    return has_people_first_marker and not has_product_first_marker


def visual_post_fit_score(post: dict[str, Any]) -> int:
    manual = number_value(post.get("score") or post.get("适配评分"))
    score = 42 if is_visual_reference_sample_url(_first_text(post, "post_url", "帖子链接", "样例帖子1链接")) else 0
    text = _visual_text(post)
    markers = [
        (("controller", "gamepad", "手柄", "控制器"), 14),
        (("product slot", "product-slot", "产品占位", "主体位置", "替换产品"), 16),
        (("product dominant", "product-first", "product hero", "产品主体", "产品为主", "静物", "无人物"), 14),
        (("hands", "handheld", "hand holding", "手持", "手部"), 12),
        (("desk", "desktop", "setup", "桌面", "电竞房"), 10),
        (("tv", "screen background", "console", "客厅", "电视", "主机"), 8),
        (("close-up", "detail", "特写", "细节", "材质"), 8),
        (("lifestyle", "real use", "使用场景", "生活方式"), 8),
        (("clean", "minimal", "low light", "低亮度", "氛围光"), 6),
        (("ig photo", "fb photo", "image post", "图片帖", "静态图"), 6),
    ]
    for terms, weight in markers:
        if any(term in text for term in terms):
            score += weight
    risk_text = " ".join([text, text_value(post.get("risk")), text_value(post.get("风险备注"))]).lower()
    if any(term in risk_text for term in ("youtube", "video", "reel", "shorts", "视频", "授权 ip", "未授权")):
        score -= 12
    if manual:
        score = max(score, int(manual))
    if is_people_first_visual_post(post):
        score = min(score, 40)
    return max(0, min(100, score))


def build_kol_visual_post_candidates(
    strategies: list[dict[str, Any]],
    visual_posts: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]] | None = None,
    *,
    week_start: str | None = None,
    now: str | None = None,
    per_brand: int = 5,
    min_score: int = 70,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = _week_start(week_start, now)
    batch_id = _batch_id("kolvp", start)
    existing_urls: set[str] = set()
    for item in existing_candidates or []:
        fields = _fields(item)
        for name in ("账号链接", "样例帖子1链接", "样例帖子2链接"):
            value = text_value(fields.get(name)).lower()
            if value:
                existing_urls.add(value)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_brand_count: dict[str, int] = {}
    strategy_brands = _strategy_brands(strategies)
    if not strategy_brands:
        strategy_brands = ["FUNLAB", "Powkong"]

    for raw_post in visual_posts:
        post = _fields(raw_post)
        post_url = _first_text(post, "post_url", "帖子链接", "样例帖子1链接", "url")
        account_url = _first_text(post, "account_url", "账号链接", "profile_url")
        image_url = _first_text(post, "thumbnail_url", "screenshot_url", "样例帖子1图片", "参考图", "image_url")
        image_key = _first_text(post, "thumbnail_image_key", "screenshot_image_key", "image_key", "样例帖子1图片Key", "参考图Key")
        post_brand = _brand_key(_first_text(post, "brand", "品牌", "适配品牌"))
        if not is_visual_reference_sample_url(post_url):
            rejected.append({"post_url": post_url, "reason": "NOT_IG_FB_IMAGE_POST", "post": post})
            continue
        if _is_placeholder_visual_url(post_url):
            rejected.append({"post_url": post_url, "reason": "PLACEHOLDER_POST_URL", "post": post})
            continue
        if not image_url:
            rejected.append({"post_url": post_url, "reason": "MISSING_REFERENCE_IMAGE", "post": post})
            continue
        if post_url.lower() in existing_urls:
            rejected.append({"post_url": post_url, "reason": "DUPLICATE_POST_URL", "post": post})
            continue
        if is_people_first_visual_post(post):
            rejected.append({"post_url": post_url, "reason": "PEOPLE_FIRST_IMAGE", "post": post})
            continue

        target_brands = [brand for brand in strategy_brands if not post_brand or _brand_key(brand) == post_brand]
        if not target_brands:
            rejected.append({"post_url": post_url, "reason": "BRAND_NOT_IN_STRATEGY", "post": post})
            continue

        fit_score = visual_post_fit_score(post)
        if fit_score < min_score:
            rejected.append({"post_url": post_url, "reason": "VISUAL_SCORE_TOO_LOW", "score": fit_score, "post": post})
            continue

        for brand in target_brands:
            key = _brand_key(brand)
            if by_brand_count.get(key, 0) >= per_brand:
                continue
            product_scope = _strategy_product_text(strategies, brand)
            tags = _visual_tags(post.get("visual_tags") or post.get("视觉标签"))
            scene = _first_text(post, "scene_type", "场景类型", "内容风格") or ", ".join(tags)
            borrow = _first_text(post, "borrow", "可借鉴元素") or scene
            avoid = _first_text(post, "avoid", "禁止复制元素") or "不复制博主原图、人物、logo、文案、水印、屏幕 UI 或未授权 IP。"
            reason = _first_text(post, "reason", "入选原因")
            account_name = _first_text(post, "account_name", "账号名称", "handle") or urlparse(account_url or post_url).netloc
            candidate = {
                "候选标题": f"{start.isoformat()} {brand} 图片帖参考 · {account_name}",
                "品牌": "FUNLAB" if key == "FUNLAB" else "Powkong",
                "平台": _platform_from_url(post_url, post.get("platform") or post.get("平台")),
                "账号名称": account_name,
                "账号链接": account_url or post_url,
                "粉丝量": _first_text(post, "followers", "粉丝量") or "待确认",
                "国家/地区": _first_text(post, "country", "国家/地区") or "待确认",
                "语言": _first_text(post, "language", "语言") or "EN",
                "内容风格": borrow,
                "适配产品": _first_text(post, "product_scope", "适配产品") or product_scope,
                "入选原因": reason or f"图片帖通过视觉迁移门槛，适配评分 {fit_score}；适合提取场景、构图、光线和产品占位。",
                "样例帖子1链接": post_url,
                "样例帖子2链接": _first_text(post, "sample_post_2", "样例帖子2链接"),
                "样例帖子1图片": image_url,
                "样例帖子1图片Key": image_key,
                "样例帖子2图片": _first_text(post, "thumbnail_url_2", "screenshot_url_2", "样例帖子2图片"),
                "样例帖子2图片Key": _first_text(post, "thumbnail_image_key_2", "screenshot_image_key_2", "样例帖子2图片Key"),
                "风险备注": "；".join(part for part in [avoid, KOL_VISUAL_GATE_NOTE] if part),
                "适配评分": fit_score,
                "发现批次": batch_id,
                "审核状态": "待确认",
                "反馈动作": "",
                "retry_round": 0,
                "状态": "待确认",
            }
            accepted.append(candidate)
            existing_urls.add(post_url.lower())
            by_brand_count[key] = by_brand_count.get(key, 0) + 1
    return accepted, rejected


def _week_start(raw: str | None, now: str | None = None) -> date:
    value = raw or now
    parsed: date | None = None
    if value:
        try:
            parsed_dt = datetime.fromisoformat(text_value(value).replace("Z", "+00:00"))
            parsed = parsed_dt.date()
        except ValueError:
            try:
                parsed = datetime.strptime(text_value(value), "%Y-%m-%d").date()
            except ValueError:
                parsed = None
    parsed = parsed or datetime.now(timezone.utc).date()
    return parsed - timedelta(days=parsed.weekday())


def _batch_id(prefix: str, week_start: date) -> str:
    raw = f"{prefix}:{week_start.isoformat()}:{DISCOVERY_VERSION}"
    return f"{prefix}-{week_start.isoformat()}-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def _split_pool(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                values.append(
                    text_value(
                        item.get("value")
                        or item.get("label")
                        or item.get("text")
                        or item.get("ERP SKU")
                        or item.get("品牌型号V2")
                        or item.get("品牌型号")
                        or item.get("product_record_id")
                    )
                )
            else:
                values.append(text_value(item))
        return [value for value in values if value]
    value = text_value(raw)
    if not value:
        return []
    return [part.strip() for part in re.split(r"[\n\r,，;；|/]+", value) if part.strip()]


def _brand_key(brand: Any) -> str:
    return "FUNLAB" if text_value(brand).upper() == "FUNLAB" else "POWKONG"


def _account_key(fields: dict[str, Any]) -> str:
    brand = _brand_key(fields.get("品牌"))
    platform = select_value(fields.get("平台")) or text_value(fields.get("平台")) or "Instagram"
    platform_key = "FB" if platform.lower().startswith("facebook") else "IG"
    return f"{brand}_{platform_key}"


def _account_fields(record_or_fields: dict[str, Any]) -> dict[str, Any]:
    fields = dict(_fields(record_or_fields))
    if not text_value(fields.get("账号名称")):
        fields["账号名称"] = text_value(fields.get("账号")) or _account_key(fields)
    if not select_value(fields.get("品牌")):
        fields["品牌"] = "FUNLAB" if "funlab" in text_value(fields.get("账号名称")).lower() else "Powkong"
    if not select_value(fields.get("平台")):
        fields["平台"] = "Facebook" if "fb" in text_value(fields.get("账号名称")).lower() else "Instagram"
    return fields


def normalize_account_records(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = accounts or [{"fields": item} for item in DEFAULT_ACCOUNT_SLOTS]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {_account_key(item) for item in DEFAULT_ACCOUNT_SLOTS}
    for item in source:
        fields = _account_fields(item)
        key = _account_key(fields)
        if key not in allowed:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append({"record_id": _record_id(item), "fields": fields})
    if len(result) >= 4:
        order = {key: idx for idx, key in enumerate(_account_key(item) for item in DEFAULT_ACCOUNT_SLOTS)}
        return sorted(result, key=lambda item: order.get(_account_key(item["fields"]), 99))[:4]
    for item in DEFAULT_ACCOUNT_SLOTS:
        key = _account_key(item)
        if key not in seen:
            seen.add(key)
            result.append({"fields": item})
    order = {key: idx for idx, key in enumerate(_account_key(item) for item in DEFAULT_ACCOUNT_SLOTS)}
    return sorted(result, key=lambda item: order.get(_account_key(item["fields"]), 99))[:4]


def normalize_product_index_record(record_or_fields: dict[str, Any], *, brand_hint: str = "") -> dict[str, str]:
    fields = _fields(record_or_fields)
    brand = select_value(fields.get("品牌")) or brand_hint
    product_record_id = _record_id(record_or_fields) or text_value(fields.get("产品库记录ID") or fields.get("product_record_id"))
    erp_sku = text_value(fields.get("ERP SKU") or fields.get("老库ERP SKU") or fields.get("SKU"))
    brand_model_v2 = text_value(fields.get("品牌型号V2"))
    legacy_brand_model = text_value(fields.get("品牌型号"))
    brand_model = text_value(brand_model_v2 or legacy_brand_model or fields.get("品牌型号/SKU") or fields.get("型号"))
    product_name = (
        text_value(fields.get("产品中文名"))
        or text_value(fields.get("型号中文名"))
        or text_value(fields.get("产品名"))
        or text_value(fields.get("产品名称"))
        or brand_model
        or erp_sku
    )
    product_en = text_value(fields.get("产品英文名") or fields.get("型号英文名"))
    label_parts = [part for part in (product_name, brand_model, erp_sku) if part]
    label = text_value(fields.get("下拉显示名") or fields.get("选项标签")) or " / ".join(label_parts)
    search_key = " ".join([brand, product_name, product_en, brand_model, brand_model_v2, legacy_brand_model, erp_sku, product_record_id])
    return {
        "product_record_id": product_record_id,
        "品牌": brand,
        "产品名": product_name,
        "产品英文名": product_en,
        "品牌型号": brand_model,
        "品牌型号V2": brand_model_v2,
        "旧品牌型号": legacy_brand_model,
        "ERP SKU": erp_sku,
        "下拉显示名": label,
        "search_key": search_key,
    }


def _match_one_product(raw: str, product_index: list[dict[str, Any]], brand: str) -> dict[str, str] | None:
    needle = _norm(raw)
    if not needle:
        return None
    exact_fields = ("product_record_id", "ERP SKU", "品牌型号V2", "品牌型号", "旧品牌型号")
    normalized = [normalize_product_index_record(item) for item in product_index]
    same_brand = [item for item in normalized if not item.get("品牌") or _brand_key(item.get("品牌")) == _brand_key(brand)]
    for item in same_brand + normalized:
        if any(needle == _norm(item.get(field)) for field in exact_fields):
            return item
    for item in same_brand + normalized:
        if needle and needle in _norm(item.get("search_key")):
            return item
    return None


def resolve_product_pool(raw: Any, product_index: list[dict[str, Any]], brand: str, fallback: Any = "") -> dict[str, Any]:
    requested = _split_pool(raw) or _split_pool(fallback)
    matched: list[dict[str, str]] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for item in requested:
        match = _match_one_product(item, product_index, brand)
        if not match:
            unmatched.append(item)
            continue
        key = match.get("product_record_id") or match.get("ERP SKU") or match.get("品牌型号") or match.get("产品名")
        if key in seen:
            continue
        seen.add(key)
        matched.append(match)
    product_names = [item.get("产品名") or item.get("品牌型号") or item.get("ERP SKU") for item in matched]
    return {
        "requested": requested,
        "matched": matched,
        "unmatched": unmatched,
        "product_pool_text": "\n".join([item for item in product_names if item]),
        "record_ids": "\n".join([item.get("product_record_id", "") for item in matched if item.get("product_record_id")]),
        "sku_text": "\n".join([item.get("ERP SKU", "") for item in matched if item.get("ERP SKU")]),
        "label_text": "\n".join([item.get("下拉显示名", "") for item in matched if item.get("下拉显示名")]),
    }


def build_weekly_input_card(
    accounts: list[dict[str, Any]],
    product_index: list[dict[str, Any]],
    *,
    week_start: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    start = _week_start(week_start, now)
    normalized_accounts = normalize_account_records(accounts)
    options_by_brand: dict[str, list[dict[str, str]]] = {"FUNLAB": [], "POWKONG": []}
    for item in product_index:
        product = normalize_product_index_record(item)
        options_by_brand.setdefault(_brand_key(product.get("品牌")), []).append(product)

    card_accounts = []
    for account in normalized_accounts:
        fields = account["fields"]
        brand_key = _brand_key(fields.get("品牌"))
        default_pool = text_value(fields.get("本周主推产品池") or fields.get("默认产品池") or fields.get("产品池"))
        options = options_by_brand.get(brand_key, [])[:50]
        card_accounts.append(
            {
                "account_key": _account_key(fields),
                "账号名称": text_value(fields.get("账号名称")),
                "品牌": "FUNLAB" if brand_key == "FUNLAB" else "Powkong",
                "平台": select_value(fields.get("平台")),
                "默认产品池": default_pool,
                "默认业务重点": text_value(fields.get("本周业务重点") or DEFAULT_WEEKLY_BUSINESS_FOCUS),
                "product_options": [
                    {
                        "label": item["下拉显示名"],
                        "value": item["product_record_id"] or item["ERP SKU"] or item["品牌型号"],
                        "erp_sku": item["ERP SKU"],
                        "brand_model": item["品牌型号"],
                        "product_record_id": item["product_record_id"],
                    }
                    for item in options
                ],
            }
        )
    return {
        "title": f"[SEO/P2] FB/IG 本周策略确认 · {start.isoformat()}",
        "plan_version": DISCOVERY_VERSION,
        "week_start": start.isoformat(),
        "deadline_bj": f"{start.isoformat()} 11:00",
        "description": "运营只需确认本周主推产品池和业务重点；不提交则按默认策略锁定。",
        "accounts": card_accounts,
        "actions": [
            {"label": "提交本周策略", "action": "submit_weekly_strategy"},
            {"label": "全部使用默认", "action": "use_default_weekly_strategy"},
        ],
    }


def _card_plain(content: Any) -> dict[str, str]:
    return {"tag": "plain_text", "content": text_value(content)}


def _card_md(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _card_img(img_key: str, alt: str) -> dict[str, Any]:
    return {"tag": "img", "img_key": img_key, "alt": _card_plain(alt)}


def _form_submit_button(name: str, label: str, value: dict[str, Any], *, primary: bool = False) -> dict[str, Any]:
    return {
        "tag": "button",
        "action_type": "form_submit",
        "name": name,
        "text": _card_plain(label),
        "type": "primary" if primary else "default",
        "value": value,
    }


def _action_button(label: str, value: dict[str, Any], *, primary: bool = False, danger: bool = False) -> dict[str, Any]:
    button_type = "primary" if primary else ("danger" if danger else "default")
    return {
        "tag": "button",
        "text": _card_plain(label),
        "type": button_type,
        "value": value,
    }


def build_weekly_input_feishu_card(card: dict[str, Any]) -> dict[str, Any]:
    account_keys = [text_value(item.get("account_key")) for item in card.get("accounts", []) if item.get("account_key")]
    form_elements: list[dict[str, Any]] = []
    for account in card.get("accounts", []):
        account_key = text_value(account.get("account_key"))
        label_prefix = text_value(account.get("账号名称")) or account_key
        form_elements.append(
            {
                "tag": "input",
                "name": f"product_pool__{account_key}",
                "label": _card_plain(f"{label_prefix} 主推产品池"),
                "default_value": text_value(account.get("默认产品池")),
                "input_type": "multiline_text",
                "placeholder": _card_plain("可填中文产品名 / 品牌型号 / ERP SKU；多产品换行"),
            }
        )
        form_elements.append(
            {
                "tag": "input",
                "name": f"business_focus__{account_key}",
                "label": _card_plain(f"{label_prefix} 业务重点"),
                "default_value": text_value(account.get("默认业务重点")),
                "input_type": "multiline_text",
                "placeholder": _card_plain("例如：主推新品、清库存、测某类场景内容"),
            }
        )
    form_elements.extend(
        [
            _form_submit_button(
                "fbig_weekly_input_submit",
                "提交本周策略",
                {
                    "action": "fbig_weekly_input",
                    "mode": "submit",
                    "week_start": card.get("week_start"),
                    "account_keys": account_keys,
                    "source": "weekly_strategy_card_v1",
                },
                primary=True,
            ),
            _form_submit_button(
                "fbig_weekly_input_default",
                "全部使用默认",
                {
                    "action": "fbig_weekly_input",
                    "mode": "default",
                    "use_default": True,
                    "week_start": card.get("week_start"),
                    "account_keys": account_keys,
                    "source": "weekly_strategy_card_v1",
                },
            ),
        ]
    )
    accounts_md = []
    for account in card.get("accounts", []):
        product_count = len(account.get("product_options") or [])
        accounts_md.append(
            f"- **{text_value(account.get('账号名称'))}**：默认产品池 `{text_value(account.get('默认产品池')) or '未设置'}`；"
            f"可选产品索引 {product_count} 条"
        )
    return {
        "config": {"wide_screen_mode": True, "enable_forward": False},
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": text_value(card.get("title"))},
        },
        "elements": [
            _card_md(
                "\n".join(
                    [
                        f"**周次**：{text_value(card.get('week_start'))}",
                        f"**截止**：{text_value(card.get('deadline_bj'))} BJ；不提交则按默认策略锁定。",
                        "**范围**：只确认本周主推产品池和业务重点；SEO/GEO、内容支柱、参考对象由 AI 补全。",
                    ]
                )
            ),
            {"tag": "hr"},
            _card_md("**四账号默认策略**\n" + "\n".join(accounts_md)),
            {"tag": "hr"},
            _card_md("运营可直接改输入框；产品池优先填下拉显示名、品牌型号或 ERP SKU，系统会自动映射产品库。"),
            {"tag": "form", "name": "fbig_weekly_input_form", "elements": form_elements},
        ],
    }


def lock_weekly_strategies(
    accounts: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    product_index: list[dict[str, Any]],
    *,
    week_start: str | None = None,
    now: str | None = None,
) -> list[dict[str, Any]]:
    start = _week_start(week_start, now)
    by_key: dict[str, dict[str, Any]] = {}
    for submission in submissions:
        fields = _fields(submission)
        key = text_value(fields.get("account_key")) or _account_key(fields)
        by_key[key] = fields

    strategies: list[dict[str, Any]] = []
    for account in normalize_account_records(accounts):
        account_fields = account["fields"]
        key = _account_key(account_fields)
        submitted = by_key.get(key, {})
        brand = select_value(account_fields.get("品牌"))
        platform = select_value(account_fields.get("平台"))
        product_raw = (
            submitted.get("本周主推产品池")
            or submitted.get("product_pool")
            or submitted.get("products")
            or submitted.get("ERP SKU")
            or submitted.get("erp_sku")
        )
        default_pool = account_fields.get("默认产品池") or account_fields.get("产品池") or account_fields.get("本周主推产品池")
        resolved = resolve_product_pool(product_raw, product_index, brand, fallback=default_pool)
        business_focus = text_value(submitted.get("本周业务重点") or submitted.get("business_focus")) or text_value(
            account_fields.get("本周业务重点")
        ) or DEFAULT_WEEKLY_BUSINESS_FOCUS
        product_pool = resolved["product_pool_text"] or text_value(default_pool) or f"{brand} hero product"
        submit_status = "运营已提交" if submitted else "默认锁定"
        source = "operator" if submitted else "default"
        strategies.append(
            {
                "策略标题": f"{start.isoformat()} {text_value(account_fields.get('账号名称'))} 周策略",
                "周次": start.isoformat(),
                "账号名称": text_value(account_fields.get("账号名称")),
                "品牌": brand,
                "平台": platform,
                "每周候选数": int(number_value(account_fields.get("每周候选数") or account_fields.get("每周上限"), 3)),
                "内容支柱配比": text_value(account_fields.get("内容支柱配比")) or "产品场景\n卖点教育\nUGC/KOL社证",
                "目标信号优先级": text_value(account_fields.get("目标信号优先级")) or "Saves\nShares\nComments",
                "产品池": product_pool,
                "本周主推产品池": resolved["label_text"] or product_pool,
                "本周业务重点": business_focus,
                "提交状态": submit_status,
                "策略来源": source,
                "产品池记录ID列表": resolved["record_ids"],
                "产品池ERP SKU列表": resolved["sku_text"],
                "产品池解析结果": json.dumps(resolved["matched"], ensure_ascii=False),
                "未匹配产品": "\n".join(resolved["unmatched"]),
                "SEO主题簇": text_value(submitted.get("SEO主题簇") or account_fields.get("SEO主题簇")),
                "GEO问题池": text_value(submitted.get("GEO问题池") or account_fields.get("GEO问题池")),
                "Hashtag分层池": text_value(submitted.get("Hashtag分层池") or account_fields.get("Hashtag分层池")),
                "参考对象策略": "产品保真优先；只借鉴场景、构图、灯光和叙事，不复制竞品产品外观。",
                "AI补全说明": (
                    "运营只填业务重点和产品池；AI 按品牌策略补内容支柱、SEO/GEO、参考对象和实验变量。"
                    + (" 未匹配产品需运营修正。" if resolved["unmatched"] else "")
                ),
                "状态": "启用",
            }
        )
    return strategies


def build_product_index_fields(product_records: list[dict[str, Any]], *, brand_hint: str = "") -> list[dict[str, Any]]:
    rows = []
    for record in product_records:
        product = normalize_product_index_record(record, brand_hint=brand_hint)
        if not (product["产品名"] or product["ERP SKU"] or product["品牌型号"]):
            continue
        rows.append(
            {
                "产品库记录ID": product["product_record_id"],
                "品牌": product["品牌"] or brand_hint,
                "产品名": product["产品名"],
                "产品英文名": product["产品英文名"],
                "品牌型号": product["品牌型号"],
                "ERP SKU": product["ERP SKU"],
                "下拉显示名": product["下拉显示名"],
                "检索关键词": product["search_key"],
                "状态": "可选",
            }
        )
    return rows


def _strategy_brands(strategies: list[dict[str, Any]]) -> list[str]:
    brands: list[str] = []
    for strategy in strategies:
        brand = _brand_key(_fields(strategy).get("品牌"))
        if brand not in brands:
            brands.append(brand)
    return brands or ["FUNLAB", "POWKONG"]


def _strategy_product_text(strategies: list[dict[str, Any]], brand: str) -> str:
    products: list[str] = []
    for strategy in strategies:
        fields = _fields(strategy)
        if _brand_key(fields.get("品牌")) != _brand_key(brand):
            continue
        products.extend(_split_pool(fields.get("产品池")))
    return "\n".join(dict.fromkeys(products))


def build_reference_discovery_candidates(
    strategies: list[dict[str, Any]],
    existing_references: list[dict[str, Any]],
    *,
    week_start: str | None = None,
    now: str | None = None,
    limit_per_brand: int = 6,
) -> list[dict[str, Any]]:
    start = _week_start(week_start, now)
    batch_id = _batch_id("ref", start)
    existing_urls = {text_value(_fields(item).get("账号/帖子URL")).lower() for item in existing_references}
    candidates: list[dict[str, Any]] = []
    for brand in _strategy_brands(strategies):
        product_scope = _strategy_product_text(strategies, brand)
        count = 0
        for seed in REFERENCE_SEEDS.get(_brand_key(brand), []):
            if text_value(seed["url"]).lower() in existing_urls:
                continue
            fields = {
                "参考标题": seed["title"],
                "品牌": "FUNLAB" if _brand_key(brand) == "FUNLAB" else "Powkong",
                "平台": seed["platform"],
                "参考类型": seed["type"],
                "账号/帖子URL": seed["url"],
                "内容支柱": seed["pillar"],
                "可借鉴元素": seed["borrow"],
                "禁止复制元素": seed["avoid"],
                "适用品类/产品": product_scope,
                "视觉标签": seed["tags"],
                "风险备注": "待运营确认；不得直接复制图片、logo、文案或未授权角色。",
                "来源类型": "AI周发现",
                "发现批次": batch_id,
                "AI推荐理由": seed["reason"],
                "品牌适配评分": seed["score"],
                "视觉可迁移评分": seed["score"],
                "状态": "待确认",
                "审核状态": "待确认",
            }
            candidates.append(fields)
            count += 1
            if count >= limit_per_brand:
                break
    return candidates


def build_kol_candidates(
    strategies: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]] | None = None,
    *,
    week_start: str | None = None,
    now: str | None = None,
    per_brand: int = 5,
    offset_by_brand: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    start = _week_start(week_start, now)
    batch_id = _batch_id("kol", start)
    existing_urls = {text_value(_fields(item).get("账号链接")).lower() for item in (existing_candidates or [])}
    offset_by_brand = offset_by_brand or {}
    result: list[dict[str, Any]] = []
    for brand in _strategy_brands(strategies):
        key = _brand_key(brand)
        product_scope = _strategy_product_text(strategies, brand)
        seeds = KOL_SEEDS.get(key, [])
        offset = offset_by_brand.get(key, 0)
        selected = 0
        for index in range(len(seeds)):
            seed = seeds[(index + offset) % len(seeds)]
            sample_links = _kol_visual_sample_links(seed)
            if not sample_links:
                continue
            if text_value(seed["url"]).lower() in existing_urls and selected < len(seeds):
                continue
            visual_style = text_value(seed.get("visual_style")) or text_value(seed.get("style"))
            visual_reason = text_value(seed.get("visual_reason")) or text_value(seed.get("reason"))
            risk = text_value(seed.get("risk"))
            risk = "；".join(
                part
                for part in [
                    risk,
                    "只借鉴 IG/FB 图片帖的构图、光线、场景和道具关系，不复制原图、人物、logo、文案或未授权 IP。",
                    KOL_VISUAL_GATE_NOTE,
                ]
                if part
            )
            result.append(
                {
                    "候选标题": f"{start.isoformat()} {brand} KOL · {seed['handle']}",
                    "品牌": "FUNLAB" if key == "FUNLAB" else "Powkong",
                    "平台": seed["platform"],
                    "账号名称": seed["handle"],
                    "账号链接": seed["url"],
                    "粉丝量": seed["followers"],
                    "国家/地区": seed["country"],
                    "语言": seed["language"],
                    "内容风格": visual_style,
                    "适配产品": product_scope,
                    "入选原因": visual_reason,
                    "样例帖子1链接": sample_links[0],
                    "样例帖子2链接": sample_links[1] if len(sample_links) > 1 else "",
                    "样例帖子1图片": text_value(seed.get("sample_image_1")),
                    "样例帖子2图片": text_value(seed.get("sample_image_2")),
                    "风险备注": risk,
                    "适配评分": seed["score"],
                    "发现批次": batch_id,
                    "审核状态": "待确认",
                    "反馈动作": "",
                    "retry_round": offset,
                    "状态": "待确认",
                }
            )
            selected += 1
            if selected >= per_brand:
                break
    return result


def build_kol_review_cards(candidates: list[dict[str, Any]], *, week_start: str | None = None) -> list[dict[str, Any]]:
    by_brand: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        fields = _fields(candidate)
        by_brand.setdefault(_brand_key(fields.get("品牌")), []).append(fields)
    cards = []
    for brand_key, items in by_brand.items():
        brand = "FUNLAB" if brand_key == "FUNLAB" else "Powkong"
        cards.append(
            {
                "title": f"[SEO/P2] {brand} 本周 KOL 参考候选",
                "brand": brand,
                "week_start": text_value(week_start),
                "description": "每个账号单独反馈；不合适几个就补几个新候选。",
                "candidates": [
                    {
                        "candidate_record_id": _record_id(item),
                        "账号名称": text_value(item.get("账号名称")),
                        "账号链接": text_value(item.get("账号链接")),
                        "平台": select_value(item.get("平台")) or text_value(item.get("平台")),
                        "粉丝量": text_value(item.get("粉丝量")),
                        "国家/地区": text_value(item.get("国家/地区")),
                        "语言": text_value(item.get("语言")),
                        "适配产品": text_value(item.get("适配产品")),
                        "入选原因": text_value(item.get("入选原因")),
                        "样例帖子": [text_value(item.get("样例帖子1链接")), text_value(item.get("样例帖子2链接"))],
                        "样例图片": [text_value(item.get("样例帖子1图片")), text_value(item.get("样例帖子2图片"))],
                        "样例图片Keys": [text_value(item.get("样例帖子1图片Key")), text_value(item.get("样例帖子2图片Key"))],
                        "内容风格": text_value(item.get("内容风格")),
                        "风险备注": text_value(item.get("风险备注")),
                        "actions": [
                            {"label": "合适入库", "action": "approve"},
                            {"label": "不合适换一个", "action": "reject_replace"},
                            {"label": "暂存观察", "action": "hold"},
                            {"label": "屏蔽类似", "action": "block_similar"},
                        ],
                    }
                    for item in items[:5]
                ],
            }
        )
    return cards


def build_kol_feishu_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feishu_cards: list[dict[str, Any]] = []
    for card in cards:
        elements: list[dict[str, Any]] = [
            _card_md(
                "\n".join(
                    [
                        f"**品牌**：{text_value(card.get('brand'))}",
                        f"**周次**：{text_value(card.get('week_start')) or '未指定'}",
                        "**这张卡用来做什么**：筛选可借鉴的 IG/FB 静态图片帖，沉淀为后续生图的设计参考；不是筛 KOL 合作对象，也不会直接发帖。",
                        "**运营只需要判断**：这张参考图的场景、构图、光线、产品占位是否适合套用到 FUNLAB/POWKONG 产品。",
                        f"**筛选门槛**：{KOL_VISUAL_GATE_NOTE}",
                    ]
                )
            ),
            {"tag": "hr"},
        ]
        for index, candidate in enumerate(card.get("candidates", []), start=1):
            sample_links = [
                item
                for item in candidate.get("样例帖子", [])
                if is_visual_reference_sample_url(item) and not _is_placeholder_visual_url(item)
            ]
            samples = "\n".join(f"- [来源图片帖 {sample_index}]({link})" for sample_index, link in enumerate(sample_links, start=1))
            sample_images = [item for item in candidate.get("样例图片", []) if item]
            sample_image_keys = [item for item in candidate.get("样例图片Keys", []) if item]
            elements.append(
                _card_md(
                    "\n".join(
                        [
                            f"**{index}. [{text_value(candidate.get('账号名称'))}]({text_value(candidate.get('账号链接'))})**",
                            f"平台/地区/语言：{text_value(candidate.get('平台'))} · {text_value(candidate.get('国家/地区'))} · {text_value(candidate.get('语言'))}；粉丝量：{text_value(candidate.get('粉丝量'))}",
                            f"可套用画面：{text_value(candidate.get('内容风格')) or '未填写'}",
                            f"入选原因：{text_value(candidate.get('入选原因'))}",
                            f"风险备注：{text_value(candidate.get('风险备注')) or '无'}",
                            samples or "来源图片帖：未提供；请先补真实 IG/FB 静态图片帖链接。",
                        ]
                    )
                )
            )
            if sample_image_keys:
                for image_index, image_key in enumerate(sample_image_keys[:2], start=1):
                    elements.append(_card_img(text_value(image_key), f"参考图预览 {image_index}"))
            else:
                image_preview = "\n".join(
                    f"- 参考图 URL {image_index}：{link}" for image_index, link in enumerate(sample_images, start=1)
                )
                elements.append(
                    _card_md(
                        image_preview
                        or "**参考图预览**：未内嵌图片。发送链路需要先把参考图 URL 上传到飞书，生成 `image_key` 后再发卡。"
                    )
                )
            base_value = {
                "action": "fbig_kol_action",
                "candidate_record_id": text_value(candidate.get("candidate_record_id")),
                "week_start": text_value(card.get("week_start")),
                "brand": text_value(card.get("brand")),
                "candidate_name": text_value(candidate.get("账号名称")),
                "source": "kol_review_card_v1",
            }
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        _action_button("合适入库", {**base_value, "kol_action": "approve"}, primary=True),
                        _action_button("不合适换一个", {**base_value, "kol_action": "reject_replace"}, danger=True),
                        _action_button("暂存观察", {**base_value, "kol_action": "hold"}),
                        _action_button("屏蔽类似", {**base_value, "kol_action": "block_similar"}),
                    ],
                }
            )
            elements.append({"tag": "hr"})
        feishu_cards.append(
            {
                "config": {"wide_screen_mode": True, "enable_forward": False},
                "header": {
                    "template": "yellow",
                    "title": {"tag": "plain_text", "content": text_value(card.get("title"))},
                },
                "elements": elements,
            }
        )
    return feishu_cards


def reference_fields_from_kol(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = _fields(candidate)
    brand = "FUNLAB" if _brand_key(fields.get("品牌")) == "FUNLAB" else "Powkong"
    primary_post = text_value(fields.get("样例帖子1链接")) or text_value(fields.get("账号链接"))
    primary_image = text_value(fields.get("样例帖子1图片"))
    image_quality = "合格" if is_visual_reference_sample_url(primary_post) and primary_image else "待检查"
    image_reason = (
        "已提供 IG/FB 图片帖链接和参考图预览，可作为构图/光线/场景/产品占位参考。"
        if image_quality == "合格"
        else "缺少帖子级 IG/FB 图片链接或参考图预览，入库后仍需人工补齐。"
    )
    return {
        "参考标题": f"KOL参考 · {text_value(fields.get('账号名称'))}",
        "品牌": brand,
        "平台": select_value(fields.get("平台")) or text_value(fields.get("平台")) or "Instagram",
        "参考类型": "博主图片帖",
        "账号/帖子URL": primary_post,
        "内容支柱": "UGC/KOL社证",
        "可借鉴元素": text_value(fields.get("内容风格")),
        "禁止复制元素": "不复制 KOL 原图、头像、个人口吻、未经授权评测结论或平台水印。",
        "适用品类/产品": text_value(fields.get("适配产品")),
        "视觉标签": text_value(fields.get("内容风格")),
        "视觉参考缩略图": primary_image,
        "样例图片链接": primary_image,
        "图片帖合格性": image_quality,
        "图片帖合格原因": image_reason,
        "风险备注": text_value(fields.get("风险备注")),
        "来源类型": "KOL周筛选",
        "发现批次": text_value(fields.get("发现批次")),
        "AI推荐理由": text_value(fields.get("入选原因")),
        "品牌适配评分": fields.get("适配评分") or "",
        "视觉可迁移评分": fields.get("适配评分") or "",
        "状态": "可用",
        "审核状态": "已确认",
    }


def apply_kol_action(
    candidate: dict[str, Any],
    *,
    action: str,
    strategies: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]] | None = None,
    replacement_count: int = 1,
    week_start: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    fields = _fields(candidate)
    updates: dict[str, Any] = {"反馈动作": action}
    reference_fields: dict[str, Any] | None = None
    replacements: list[dict[str, Any]] = []
    if action == "approve":
        updates.update({"审核状态": "已通过", "状态": "已入库"})
        reference_fields = reference_fields_from_kol(candidate)
    elif action == "reject_replace":
        retry_round = int(float(fields.get("retry_round") or 0)) + 1
        updates.update({"审核状态": "不合适", "状态": "已打回", "retry_round": retry_round})
        brand_key = _brand_key(fields.get("品牌"))
        replacements = build_kol_candidates(
            strategies,
            existing_candidates=[*(existing_candidates or []), candidate],
            week_start=week_start,
            now=now,
            per_brand=max(1, min(replacement_count, 5)),
            offset_by_brand={brand_key: retry_round},
        )
        replacements = [item for item in replacements if _brand_key(item.get("品牌")) == brand_key][:replacement_count]
    elif action == "hold":
        updates.update({"审核状态": "暂存观察", "状态": "暂存观察"})
    elif action == "block_similar":
        updates.update({"审核状态": "屏蔽类似", "状态": "已屏蔽"})
    else:
        raise ValueError(f"Unsupported KOL action: {action}")
    return {"updates": updates, "reference_fields": reference_fields, "replacements": replacements}


def discovery_input_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
