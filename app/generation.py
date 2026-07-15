from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from .ai_client import AiGenerationError, OpenAICompatibleClient
from .config import Settings
from .rules import bool_value, multi_value, select_value, text_value


GENERATION_VERSION = "fb-ig-gen-v1.1"

GENERATION_BLOCK_TERMS = (
    "among us",
    "kakariko",
    "mario",
    "minecraft",
    "pokemon",
    "piranha plant",
    "super smash",
    "zelda",
    "zonai",
    "nintendo official",
    "razer",
    "8bitdo",
    "gamesir",
)

IMAGE_PROMPT_FORBIDDEN_RENDER_RE = re.compile(
    r"\b(add|include|render|show|place|with)\b.{0,40}\b(logo|text overlay|watermark)\b",
    re.I,
)
IMAGE_PROMPT_FORBIDDEN_PRODUCT_CHANGE_RE = re.compile(
    r"\b(redesign|re[- ]?design|recolor|recolour|change the product|change product|"
    r"replace the product|make a new version|invent product parts|alter the product|"
    r"modify the product shape|different product)\b",
    re.I,
)

PRODUCT_REFERENCE_FIELD_NAMES = (
    "产品参考图包",
    "产品参考图",
    "产品原图",
    "产品图片",
    "产品库图片",
    "图片",
)
DESIGN_REFERENCE_FIELD_NAMES = (
    "设计参考图",
    "竞品参考图",
    "社媒参考图",
    "博主参考图",
)
DETAIL_REFERENCE_FIELD_NAMES = (
    "细节参考图",
    "按键参考图",
    "纹路参考图",
    "接口参考图",
)
DEFAULT_REFERENCE_STRATEGY = "产品保真优先"
FUNLAB_IP_ALLOWED_STATUSES = {"合规-无IP", "合规-已授权"}
FUNLAB_IP_STATUS_FIELD_NAMES = ("IP合规状态", "产品库IP合规状态")
REFERENCE_SOURCE_OF_TRUTH_RULE = (
    "Use the attached product reference image as the single source of truth for the product. "
    "Preserve the exact product shape, proportions, color, material, buttons, ports, textures, visible markings, "
    "and accessory layout. Only change the surrounding scene, lighting, camera angle, background, and composition. "
    "Do not redesign, recolor, morph, simplify, replace, or invent product parts."
)


def _has_positive_forbidden_match(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 80) : match.start()].lower()
        if any(marker in prefix for marker in ("do not ", "don't ", "never ", "no ", "not ", "without ")):
            continue
        return True
    return False

BRAND_RULES = {
    "Powkong": {
        "voice": "fun but not childish, player-aware but not technical-flexing, confident and casual.",
        "visual": (
            "bright collectible desk setup; Powkong Orange #FF9D00 as the warm accent; white or warm home desk base; "
            "colorful but controlled 60-30-10 palette; product as a characterful desk object."
        ),
        "photo_style": "GENKI-like soft product photography plus Mfish-like warm colorful desk styling.",
        "avoid": (
            "hardcore esports, Pro/Tournament positioning, cyberpunk, always-on RGB glow, Funlab purple, "
            "public Nintendo IP words, Mario, Pokemon, Piranha Plant."
        ),
        "slogan": "Gear with Character.",
        "must": "Make the product feel portable, expressive, desk-friendly, and characterful without using unauthorized IP.",
    },
    "FUNLAB": {
        "voice": "hardcore but readable, design-led, direct, controlled intensity, never candy-like.",
        "visual": (
            "Void Black #0B0B10 dominant background, Funlab Purple #9900FF glow accent, Hidden Glow reveal, "
            "low-key cinematic side light, high contrast, gaming-room atmosphere."
        ),
        "photo_style": "low-light dramatic product photography with crisp edges, visible glow contrast, and dark premium surfaces.",
        "avoid": (
            "candy color, warm toy tone, family scenes, Powkong orange, public competitor comparisons such as "
            "RAZER, 8BitDo, GameSir."
        ),
        "slogan": "Hidden Until Lit.",
        "must": "Anchor the visual in Hidden Glow, dark contrast, and a powered-on reveal moment when applicable.",
    },
}

PILLAR_JOBS = {
    "产品场景": "show a concrete setup or usage moment so the audience can picture ownership.",
    "卖点教育": "explain one practical feature in a simple visual way.",
    "UGC/KOL社证": "turn credible user or creator proof into trust without overclaiming.",
    "活动权益": "frame the offer as reducing purchase friction, not just a discount.",
    "品牌幕后": "make the design or making process feel intentional and human.",
}

EXPERIMENT_GUIDE = {
    "Hook": "Only test the opening idea or first line. Keep format, CTA, visual style, and posting time stable.",
    "Format": "Only test whether single image vs carousel framing changes response.",
    "CTA": "Only test the action request. Keep offer, proof, and visual constant.",
    "Offer": "Only test the offer framing. Do not change hook and visual style.",
    "Proof": "Only test the trust signal order or source.",
    "Visual": "Only test the image concept. Keep caption structure stable.",
    "Posting Time": "Keep content constant and only test time slot.",
}


@dataclass(frozen=True)
class GenerationPayload:
    brief: str
    hook_hypothesis: str
    caption_en: str
    hashtags_en: str
    seo_geo_note: str
    caption_cn_note: str
    image_prompt: str
    publish_checklist: str
    risk_checklist: str
    risk_level: str = "normal"


def generation_input_hash(fields: dict[str, Any]) -> str:
    keys = [
        "内容标题",
        "产品名",
        "品牌型号/SKU",
        "主推卖点",
        "产品库记录ID",
        "产品库产品简述",
        "产品库系列英文名",
        "产品库型号英文名",
        "产品库适配IP/IP联想",
        "产品库IP合规状态",
        "IP合规状态",
        "IP合规备注",
        "设计参考图",
        "产品参考图包",
        "产品参考图",
        "产品原图",
        "细节参考图",
        "参考图使用策略",
        "参考对象",
        "参考对象链接",
        "参考理由",
        "借鉴元素",
        "禁止复制元素",
        "选题来源",
        "SEO主关键词",
        "GEO目标问题",
        "搜索意图",
        "语义实体词",
        "长尾关键词",
        "目标落地页",
        "Hashtag词组池",
        "SEO/GEO生成说明",
        "品牌",
        "平台",
        "发布位置",
        "内容支柱",
        "目标信号",
        "实验变量",
        "素材类型",
        "目标链接",
        "权益内容",
    ]
    payload = {key: fields.get(key) for key in keys}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reference_images_from_fields(fields: dict[str, Any], field_names: tuple[str, ...]) -> list[Any]:
    for field_name in field_names:
        value = fields.get(field_name)
        if isinstance(value, list) and value:
            return value
        if isinstance(value, dict) and value:
            return [value]
    return []


def product_reference_images(fields: dict[str, Any]) -> list[Any]:
    return _reference_images_from_fields(fields, PRODUCT_REFERENCE_FIELD_NAMES)


def design_reference_images(fields: dict[str, Any]) -> list[Any]:
    return _reference_images_from_fields(fields, DESIGN_REFERENCE_FIELD_NAMES)


def detail_reference_images(fields: dict[str, Any]) -> list[Any]:
    return _reference_images_from_fields(fields, DETAIL_REFERENCE_FIELD_NAMES)


def has_product_reference_image(fields: dict[str, Any]) -> bool:
    return bool(product_reference_images(fields))


def funlab_ip_compliance_status(fields: dict[str, Any]) -> str:
    for field_name in FUNLAB_IP_STATUS_FIELD_NAMES:
        status = select_value(fields.get(field_name))
        if status:
            return status
    return ""


def image_generation_mode(fields: dict[str, Any]) -> str:
    return select_value(fields.get("图片生成模式")) or "Codex Image"


def image_generation_requires_reference(fields: dict[str, Any]) -> bool:
    return image_generation_mode(fields) == "Codex Image"


def funlab_ip_compliance_issue(fields: dict[str, Any]) -> str:
    brand = select_value(fields.get("品牌"))
    if brand.upper() != "FUNLAB":
        return ""
    if image_generation_mode(fields) == "不生成":
        return ""
    status = funlab_ip_compliance_status(fields)
    if not status:
        return "FUNLAB_IP_COMPLIANCE_MISSING"
    if status not in FUNLAB_IP_ALLOWED_STATUSES:
        return f"FUNLAB_IP_COMPLIANCE_BLOCKED:{status}"
    return ""


def required_generation_missing(fields: dict[str, Any]) -> list[str]:
    required = ["品牌", "平台", "内容支柱", "目标信号", "实验变量"]
    missing = [name for name in required if not text_value(fields.get(name))]
    if not (text_value(fields.get("产品名")) or text_value(fields.get("内容标题"))):
        missing.append("产品名/内容标题")
    return missing


def generation_candidate_reason(fields: dict[str, Any], *, force: bool = False) -> tuple[bool, str]:
    status = select_value(fields.get("状态"))
    if status not in {"选题中", "待审核"}:
        return False, f"status={status or 'empty'}"
    if bool_value(fields.get("文案人工锁定")) and bool_value(fields.get("图片Prompt人工锁定")):
        return False, "both_locked"
    missing = required_generation_missing(fields)
    if missing:
        return False, "missing=" + ",".join(missing)
    if image_generation_requires_reference(fields) and not has_product_reference_image(fields):
        return False, "missing_product_reference_image"
    ip_issue = funlab_ip_compliance_issue(fields)
    if ip_issue:
        return False, ip_issue
    current_hash = generation_input_hash(fields)
    existing_hash = text_value(fields.get("AI生成输入Hash"))
    gen_status = select_value(fields.get("AI生成状态"))
    if not force and existing_hash == current_hash and gen_status == "已生成":
        return False, "unchanged"
    if gen_status and gen_status not in {"待生成", "生成失败", "已生成"} and not force:
        return False, f"generation_status={gen_status}"
    return True, "candidate"


def build_prompt(fields: dict[str, Any]) -> tuple[str, str]:
    brand = select_value(fields.get("品牌")) or "Powkong"
    brand_rules = BRAND_RULES.get(brand, BRAND_RULES["Powkong"])
    platform = ", ".join(multi_value(fields.get("平台"))) or "Instagram"
    slots = ", ".join(multi_value(fields.get("发布位置"))) or "IG Feed"
    product = text_value(fields.get("产品名")) or text_value(fields.get("内容标题"))
    sku = text_value(fields.get("品牌型号/SKU"))
    pillar = select_value(fields.get("内容支柱"))
    signal = ", ".join(multi_value(fields.get("目标信号"))) or "Reach"
    experiment = select_value(fields.get("实验变量"))
    material_type = select_value(fields.get("素材类型")) or "single_image"
    selling_points = text_value(fields.get("主推卖点")) or "one clear practical benefit"
    product_brief = text_value(fields.get("产品库产品简述"))
    series_en = safe_public_context(fields.get("产品库系列英文名") or fields.get("系列英文名"))
    model_en = safe_public_context(fields.get("产品库型号英文名") or fields.get("型号英文名"))
    ip_status = funlab_ip_compliance_status(fields)
    ip_note = text_value(fields.get("产品库IP合规备注") or fields.get("IP合规备注"))
    ip_association = safe_public_context(fields.get("产品库适配IP/IP联想") or fields.get("适配IP/IP联想"))
    reference_available = has_product_reference_image(fields)
    target_url = text_value(fields.get("目标链接"))
    offer = "yes" if bool_value(fields.get("权益内容")) else "no"
    reference_object = text_value(fields.get("参考对象"))
    reference_reason = text_value(fields.get("参考理由"))
    borrow_elements = text_value(fields.get("借鉴元素"))
    forbidden_elements = text_value(fields.get("禁止复制元素"))
    seo_keyword = text_value(fields.get("SEO主关键词"))
    geo_question = text_value(fields.get("GEO目标问题"))
    search_intent = select_value(fields.get("搜索意图"))
    semantic_entities = text_value(fields.get("语义实体词"))
    long_tail_keywords = text_value(fields.get("长尾关键词"))
    landing_page = text_value(fields.get("目标落地页"))
    hashtag_pool = text_value(fields.get("Hashtag词组池"))
    seo_geo_instruction = text_value(fields.get("SEO/GEO生成说明"))

    system = (
        "You generate operational social media drafts for an ecommerce gaming accessories brand. "
        "Return valid JSON only. Do not include markdown. Do not invent certifications, discounts, "
        "collaborations, platform compatibility, or unauthorized IP names. Human review is required before publishing."
    )
    user = {
        "task": "Generate FB/IG organic content draft fields for a Feishu Bitable record.",
        "output_schema": {
            "brief": "Chinese operational brief, 2-4 short bullets.",
            "hook_hypothesis": "English one-sentence experiment hypothesis.",
            "caption_en": "English platform-native caption, concise, no banned terms.",
            "hashtags_en": "5-8 English hashtags, no unauthorized IP terms.",
            "seo_geo_note": "Chinese note explaining how caption and hashtags naturally support SEO/GEO.",
            "caption_cn_note": "Chinese explanation of why the caption works.",
            "image_prompt": "English image generation/editing prompt for a product visual candidate.",
            "publish_checklist": "Chinese checklist separated by newlines.",
            "risk_checklist": "Chinese risk checklist separated by newlines.",
            "risk_level": "normal, high-risk, or blocked",
        },
        "record": {
            "brand": brand,
            "product": product,
            "sku": sku,
            "platform": platform,
            "slots": slots,
            "material_type": material_type,
            "content_pillar": pillar,
            "primary_signal": signal,
            "experiment_variable": experiment,
            "experiment_rule": EXPERIMENT_GUIDE.get(experiment, "Only test one variable."),
            "selling_points": selling_points,
            "product_library_brief": product_brief,
            "series_en": series_en,
            "model_en": model_en,
            "product_reference_image_available": reference_available,
            "ip_compliance_status": ip_status,
            "ip_compliance_note": ip_note,
            "ip_association": ip_association,
            "target_url_present": bool(target_url),
            "target_landing_page_present": bool(landing_page),
            "offer_content": offer,
        },
        "reference_object": {
            "name": reference_object,
            "reason": reference_reason,
            "borrow_elements": borrow_elements,
            "do_not_copy": forbidden_elements,
        },
        "seo_geo": {
            "primary_keyword": seo_keyword,
            "target_geo_question": geo_question,
            "search_intent": search_intent,
            "semantic_entities": semantic_entities,
            "long_tail_keywords": long_tail_keywords,
            "landing_page": landing_page,
            "hashtag_layer_pool": hashtag_pool,
            "operator_instruction": seo_geo_instruction,
        },
        "brand_rules": brand_rules,
        "image_reference_rule": REFERENCE_SOURCE_OF_TRUTH_RULE,
        "content_job": PILLAR_JOBS.get(pillar, "make one useful brand-native post"),
        "hard_rules": [
            "For image_prompt, assume the product reference image will be attached to the image worker.",
            "The image prompt must explicitly tell the image model to preserve the exact referenced product unchanged.",
            "Only the surrounding scene, lighting, camera angle, background, and composition may change.",
            "Do not ask for a redesigned, recolored, simplified, replaced, or newly invented product.",
            "Do not use Mario, Pokemon, Piranha Plant, Zelda, Nintendo official, RAZER, 8BitDo, GameSir.",
            "Do not claim official license unless explicitly provided.",
            "Do not write cold DM copy.",
            "Do not create more than one experiment variable.",
            "Caption first sentence must naturally answer or echo the SEO primary keyword or GEO question; do not keyword-stuff.",
            "Hashtags must be selected from layered pools: brand, category, scene, intent, and community. Use 5-8 total.",
            "If a reference object is provided, borrow only scene, composition, lighting, camera angle, pacing, or mood.",
            "Do not copy competitor product appearance, logo, layout, exact caption, watermark, or protected brand/IP assets.",
            "Return seo_geo_note so operators can see why the caption and hashtags match the SEO/GEO strategy.",
            "Image prompt must not ask to render new logos, text overlays, watermarks, or unauthorized IP characters.",
            "Preserve only logos or markings already visible in the reference image.",
            "Do not expose internal IP-inspired model names or protected game-world terms in caption, hashtags, or image prompt.",
        ],
    }
    return system, json.dumps(user, ensure_ascii=False, indent=2)


def fallback_generation(fields: dict[str, Any]) -> GenerationPayload:
    brand = select_value(fields.get("品牌")) or "Powkong"
    brand_rules = BRAND_RULES.get(brand, BRAND_RULES["Powkong"])
    platform = ", ".join(multi_value(fields.get("平台"))) or "Instagram"
    product = text_value(fields.get("产品名")) or text_value(fields.get("内容标题")) or "the product"
    pillar = select_value(fields.get("内容支柱")) or "产品场景"
    signal = ", ".join(multi_value(fields.get("目标信号"))) or "Reach"
    experiment = select_value(fields.get("实验变量")) or "Hook"
    selling_points = text_value(fields.get("主推卖点")) or "a cleaner, easier gaming setup"
    material_type = select_value(fields.get("素材类型")) or "single_image"
    product_brief = text_value(fields.get("产品库产品简述"))
    product_detail = f" Product library note: {product_brief}." if product_brief else ""
    seo_keyword = text_value(fields.get("SEO主关键词"))
    geo_question = text_value(fields.get("GEO目标问题"))
    search_intent = select_value(fields.get("搜索意图"))
    hashtag_pool = normalize_hashtags(text_value(fields.get("Hashtag词组池")))
    reference_object = text_value(fields.get("参考对象"))
    borrow_elements = text_value(fields.get("借鉴元素"))
    forbidden_elements = text_value(fields.get("禁止复制元素"))

    opening = f"{seo_keyword} starts with a setup that feels intentional." if seo_keyword else f"Make your setup feel more intentional with {product}."
    if geo_question:
        opening = f"If you are asking, \"{geo_question}\", start with one detail that changes how the setup feels."
    caption = f"{opening} Built around {selling_points}, {product} keeps the focus on the way you actually play."
    if signal in {"Saves", "Profile Visits"}:
        caption += " Save this for your next setup refresh."
    elif signal in {"Comments", "Shares"}:
        caption += " Which setup detail would you upgrade first?"
    elif signal in {"Link Clicks"}:
        caption += " Tap through when you are ready to compare the details."
    else:
        caption += " Designed to stand out without taking over your desk."

    product_label = brand_product_label(brand, product)
    brief = (
        f"- 内容支柱：{pillar}；目标信号：{signal}\n"
        f"- 本条只测试变量：{experiment}\n"
        f"- 产品表达重点：{selling_points}\n"
        f"- 品牌锚点：{brand_rules['slogan']}"
    )
    hook = f"Test whether leading with {selling_points} improves {signal} for {product}."
    image_prompt = (
        f"{REFERENCE_SOURCE_OF_TRUTH_RULE} "
        f"Product photography concept for {product_label}: {brand_rules['visual']} "
        f"{brand_rules.get('photo_style', '')} {brand_rules.get('must', '')}{product_detail} "
        f"Reference object to borrow from: {reference_object or 'none'}; borrow only: {borrow_elements or 'scene, light, composition'}; "
        f"do not copy: {forbidden_elements or 'competitor products, logos, text, watermark, or protected IP'}. "
        f"Show one clear use case, clean composition, product-first framing, no text, no new logo overlay, "
        f"preserve only markings already visible in the reference image, no watermark, no unauthorized IP characters. "
        f"Format: {material_type}."
    )
    checklist = "\n".join(
        [
            "确认品牌、平台、发布位置正确",
            "确认本条只测试一个实验变量",
            "确认最终素材为可发布版本且已上传公网 URL",
            "确认 caption 不含禁用词和未经授权 IP",
            "确认目标链接可打开且与内容承诺一致",
        ]
    )
    risks = "\n".join(
        [
            "不自动冷 DM",
            "不使用未授权 KOL/UGC 素材",
            "不混用 Powkong/FUNLAB 视觉调性",
            "不承诺官方授权、医疗/性能绝对化或竞品对标",
        ]
    )
    hashtags = hashtag_pool if hashtag_pool else "#GamingSetup #SwitchAccessories #DeskSetup #GameRoom #ControllerSetup #SetupInspo"
    return GenerationPayload(
        brief=brief,
        hook_hypothesis=hook,
        caption_en=caption,
        hashtags_en=hashtags,
        seo_geo_note=(
            f"SEO/GEO：首句自然承接 `{seo_keyword or geo_question or product}`，搜索意图={search_intent or '未指定'}；"
            "hashtag 采用品牌/品类/场景/意图/社区分层，不堆竞品词。"
        ),
        caption_cn_note=f"围绕 {pillar} 和 {signal} 写，保留 {experiment} 作为唯一变量，避免外链硬广感。",
        image_prompt=image_prompt,
        publish_checklist=checklist,
        risk_checklist=risks,
        risk_level="normal",
    )


def parse_ai_json(raw: str) -> GenerationPayload:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise AiGenerationError("AI response is not JSON")
        data = json.loads(match.group(0))
    payload = GenerationPayload(
        brief=text_value(data.get("brief")),
        hook_hypothesis=text_value(data.get("hook_hypothesis")),
        caption_en=text_value(data.get("caption_en")),
        hashtags_en=text_value(data.get("hashtags_en")),
        seo_geo_note=text_value(data.get("seo_geo_note")),
        caption_cn_note=text_value(data.get("caption_cn_note")),
        image_prompt=text_value(data.get("image_prompt")),
        publish_checklist=text_value(data.get("publish_checklist")),
        risk_checklist=text_value(data.get("risk_checklist")),
        risk_level=text_value(data.get("risk_level")) or "normal",
    )
    return harden_generation_payload(payload)


def harden_generation_payload(payload: GenerationPayload) -> GenerationPayload:
    hashtags = normalize_hashtags(payload.hashtags_en)
    image_prompt = text_value(payload.image_prompt)
    seo_geo_note = text_value(payload.seo_geo_note) or "SEO/GEO：使用自然语义承接关键词或目标问题，hashtag 按品牌/品类/场景/意图/社区分层。"
    image_lower = image_prompt.lower()
    if not ("reference image" in image_lower and "preserve" in image_lower):
        image_prompt = f"{REFERENCE_SOURCE_OF_TRUTH_RULE} {image_prompt}".strip()
        image_lower = image_prompt.lower()
    safety_parts = []
    if not any(marker in image_lower for marker in ("no text", "without text", "avoid text")):
        safety_parts.append("no text")
    if not any(marker in image_lower for marker in ("no logo", "no new logo", "no logo overlay", "without logo", "avoid logo")):
        safety_parts.append("no new logo overlay")
    if "watermark" not in image_lower:
        safety_parts.append("no watermark")
    if safety_parts:
        suffix = (
            " Safety constraints: "
            + ", ".join(safety_parts)
            + ", preserve only logos or markings already visible in the reference image, no unauthorized IP characters."
        )
        image_prompt = (image_prompt.rstrip(". ") + "." + suffix).strip()
    return GenerationPayload(
        brief=payload.brief,
        hook_hypothesis=payload.hook_hypothesis,
        caption_en=payload.caption_en,
        hashtags_en=hashtags,
        seo_geo_note=seo_geo_note,
        caption_cn_note=payload.caption_cn_note,
        image_prompt=image_prompt,
        publish_checklist=payload.publish_checklist,
        risk_checklist=payload.risk_checklist,
        risk_level=payload.risk_level,
    )


def normalize_hashtags(raw: str) -> str:
    hashtags = text_value(raw)
    tokens = []
    for item in re.split(r"[,;，；\n\r\t ]+", hashtags):
        cleaned = re.sub(r"[^0-9A-Za-z_]", "", item.lstrip("#")).strip("_")
        if cleaned:
            tokens.append("#" + cleaned[:40])
    if not tokens:
        tokens = ["#GamingSetup", "#DeskSetup", "#SwitchAccessories", "#GameRoom", "#SetupInspo"]
    return " ".join(tokens[:8])


def contains_generation_block_term(raw: Any) -> bool:
    text = text_value(raw).lower()
    return any(term in text for term in GENERATION_BLOCK_TERMS)


def safe_public_context(raw: Any) -> str:
    value = text_value(raw)
    return "" if contains_generation_block_term(value) else value


def brand_product_label(brand: str, product: str) -> str:
    brand_clean = text_value(brand)
    product_clean = text_value(product)
    if not brand_clean:
        return product_clean
    if product_clean.lower().startswith(brand_clean.lower()):
        return product_clean
    return f"{brand_clean} {product_clean}".strip()


async def generate_payload(fields: dict[str, Any], settings: Settings) -> tuple[GenerationPayload, str]:
    if settings.generation_ai_enabled():
        system, user = build_prompt(fields)
        client = OpenAICompatibleClient(
            base_url=settings.generation_ai_base_url,
            api_key=settings.generation_ai_api_key,
            model=settings.generation_ai_model,
            timeout_seconds=settings.generation_ai_timeout_seconds,
        )
        raw = await client.chat_json(system=system, user=user)
        try:
            return parse_ai_json(raw), settings.generation_ai_model
        except (json.JSONDecodeError, AiGenerationError):
            return fallback_generation(fields), f"{settings.generation_ai_model}+template-fallback-json"
    return fallback_generation(fields), "template"


def validate_generation_payload(payload: GenerationPayload, source_fields: dict[str, Any] | None = None) -> list[str]:
    required_fields = {
        "brief": payload.brief,
        "hook_hypothesis": payload.hook_hypothesis,
        "caption_en": payload.caption_en,
        "hashtags_en": payload.hashtags_en,
        "seo_geo_note": payload.seo_geo_note,
        "caption_cn_note": payload.caption_cn_note,
        "image_prompt": payload.image_prompt,
        "publish_checklist": payload.publish_checklist,
        "risk_checklist": payload.risk_checklist,
    }
    issues = [f"{name.upper()}_EMPTY" for name, value in required_fields.items() if not text_value(value)]

    risk_level = text_value(payload.risk_level)
    if risk_level not in {"normal", "high-risk", "blocked"}:
        issues.append("RISK_LEVEL_INVALID")

    if "#" not in payload.hashtags_en:
        issues.append("HASHTAG_FORMAT_INVALID")
    if source_fields and any(text_value(source_fields.get(name)) for name in ("SEO主关键词", "GEO目标问题", "Hashtag词组池")):
        hashtag_count = len([token for token in payload.hashtags_en.split() if token.startswith("#")])
        if hashtag_count < 5 or hashtag_count > 8:
            issues.append("HASHTAG_LAYER_COUNT_INVALID")

    caption_lower = payload.caption_en.lower()
    for term in GENERATION_BLOCK_TERMS:
        if term in caption_lower:
            issues.append(f"CAPTION_BLOCK_TERM:{term}")

    hashtags_lower = payload.hashtags_en.lower()
    for term in GENERATION_BLOCK_TERMS:
        if term in hashtags_lower:
            issues.append(f"HASHTAG_BLOCK_TERM:{term}")

    image_lower = payload.image_prompt.lower()
    has_no_text_guard = any(marker in image_lower for marker in ("no text", "without text", "avoid text"))
    has_no_logo_guard = any(
        marker in image_lower
        for marker in ("no logo", "no new logo", "no logo overlay", "without logo", "avoid logo")
    )
    if not has_no_text_guard or not has_no_logo_guard:
        issues.append("IMAGE_PROMPT_SAFETY_GUARD_MISSING")
    if not ("reference image" in image_lower and "preserve" in image_lower):
        issues.append("IMAGE_PROMPT_REFERENCE_GUARD_MISSING")

    if _has_positive_forbidden_match(IMAGE_PROMPT_FORBIDDEN_RENDER_RE, payload.image_prompt):
        issues.append("IMAGE_PROMPT_FORBIDDEN_RENDER_INSTRUCTION")
    if _has_positive_forbidden_match(IMAGE_PROMPT_FORBIDDEN_PRODUCT_CHANGE_RE, payload.image_prompt):
        issues.append("IMAGE_PROMPT_FORBIDDEN_PRODUCT_CHANGE")

    for term in GENERATION_BLOCK_TERMS:
        if term in image_lower and f"no {term}" not in image_lower and f"without {term}" not in image_lower:
            issues.append(f"IMAGE_PROMPT_BLOCK_TERM:{term}")

    if source_fields:
        context_error = text_value(source_fields.get("_product_context_error"))
        if context_error:
            issues.append("PRODUCT_CONTEXT_ERROR")
        if image_generation_requires_reference(source_fields) and not has_product_reference_image(source_fields):
            issues.append("PRODUCT_REFERENCE_IMAGE_MISSING")
        ip_issue = funlab_ip_compliance_issue(source_fields)
        if ip_issue:
            issues.append(ip_issue)

    return issues


def build_update_fields(fields: dict[str, Any], payload: GenerationPayload, *, run_id: str, source: str) -> dict[str, Any]:
    input_hash = generation_input_hash(fields)
    caption_locked = bool_value(fields.get("文案人工锁定"))
    image_locked = bool_value(fields.get("图片Prompt人工锁定"))
    updates: dict[str, Any] = {
        "AI生成Brief": payload.brief,
        "Hook假设": payload.hook_hypothesis,
        "中文说明": payload.caption_cn_note,
        "SEO/GEO生成说明": payload.seo_geo_note,
        "发布Checklist": payload.publish_checklist,
        "风险Checklist": payload.risk_checklist,
        "审批风险等级": payload.risk_level if payload.risk_level in {"normal", "high-risk", "blocked"} else "normal",
        "AI生成状态": "人工锁定" if (caption_locked and image_locked) else "已生成",
        "AI生成版本": GENERATION_VERSION,
        "AI生成输入Hash": input_hash,
        "AI生成时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "AI生成错误": "",
        "生成来源": source,
        "运行/回放ID": run_id,
    }
    if not caption_locked:
        updates["Caption EN"] = payload.caption_en
        updates["Hashtag EN"] = payload.hashtags_en
    if not image_locked:
        updates["AI图片Prompt"] = payload.image_prompt
    status = select_value(fields.get("状态"))
    if status in {"", "选题中"}:
        updates["状态"] = "待审核"
    return updates
