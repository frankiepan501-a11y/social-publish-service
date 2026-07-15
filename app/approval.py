from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .rules import select_value, text_value


NO_CHANGE_VALUES = {"", "不改", "满意", "不改 / 满意", "不需要修改", "保持不变", "无"}


def _opt(value: str, instruction: str) -> dict[str, str]:
    return {"value": value, "label": value, "instruction": instruction}


def _dim(key: str, field_name: str, label: str, options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "key": key,
        "field_name": field_name,
        "label": label,
        "input_name": f"image_feedback_{key}",
        "type": "single_select",
        "options": [_opt("不改", "Keep this dimension unchanged.")] + options,
    }


IMAGE_FEEDBACK_DIMENSIONS = [
    _dim(
        "product_fidelity",
        "图片反馈-产品保真",
        "产品保真",
        [
            _opt("外形变形", "Restore the exact product silhouette, proportions, shell geometry, and edge contours from the product reference image."),
            _opt("颜色不对", "Match the product reference colors exactly; do not recolor the shell, buttons, sticks, or accents."),
            _opt("材质不对", "Correct surface material, finish, texture, translucency, rubber, plastic, and metallic details to match the reference product."),
            _opt("按键接口不对", "Correct button layout, stick shape, D-pad, ports, triggers, seams, and visible interface details to match the reference product."),
            _opt("图案不对", "Correct product graphics, linework, markings, and decorative pattern placement to match the reference product."),
            _opt("产品被重设计", "Use the reference image as product source of truth; do not redesign, simplify, replace, or invent product parts."),
        ],
    ),
    _dim(
        "composition",
        "图片反馈-主体构图",
        "主体构图",
        [
            _opt("主体太小", "Increase the product size in frame while preserving crop safety and platform fit."),
            _opt("主体太大", "Reduce the product scale so its full shape and surrounding context remain readable."),
            _opt("位置偏了", "Reposition the product to the intended focal area with balanced whitespace."),
            _opt("留白不足", "Add functional whitespace around the product for platform crop safety and readability."),
            _opt("裁切危险", "Keep all important product edges, hands, and key details inside safe crop margins."),
            _opt("主体不突出", "Increase visual hierarchy so the product is clearly the dominant subject."),
        ],
    ),
    _dim(
        "camera_angle",
        "图片反馈-镜头视角",
        "镜头视角",
        [
            _opt("角度不对", "Adjust the camera angle to better match the approved reference direction and product viewing angle."),
            _opt("太平", "Add more dimensional perspective instead of a flat straight-on view."),
            _opt("太俯", "Lower the camera angle so the product does not look overly top-down."),
            _opt("太仰", "Raise the camera angle so the product does not look exaggerated from below."),
            _opt("产品朝向不对", "Match the reference product orientation, hand entry direction, grip angle, and tabletop tilt more closely."),
            _opt("焦距感不对", "Adjust focal length feel and perspective compression so the product proportions look natural."),
        ],
    ),
    _dim(
        "depth_layers",
        "图片反馈-前中远景",
        "前中远景",
        [
            _opt("前景遮挡", "Remove or reduce foreground occlusion so key product details are visible."),
            _opt("中景主体不突出", "Strengthen the midground product focal layer and reduce competing elements."),
            _opt("背景太抢", "Lower background contrast, detail, or saturation so it supports the product."),
            _opt("层次不够", "Add clear foreground, subject, and background depth separation."),
            _opt("景深不自然", "Make depth of field and blur transitions natural and product-photography appropriate."),
        ],
    ),
    _dim(
        "scene_type",
        "图片反馈-场景类型",
        "场景类型",
        [
            _opt("桌搭", "Use a clean desk setup scene that feels realistic for gaming accessories."),
            _opt("电竞房", "Use a gaming room scene with restrained RGB ambience and product-first composition."),
            _opt("户外", "Use an outdoor lifestyle scene while keeping the product credible and dominant."),
            _opt("微缩景观", "Use a miniature scenic setup only if it does not distort the real product."),
            _opt("工业风", "Use an industrial tech scene with controlled materials, not heavy clutter."),
            _opt("礼物场景", "Use a gifting scene with packaging or soft props, but keep the product as focal point."),
        ],
    ),
    _dim(
        "background_surface",
        "图片反馈-背景与台面",
        "背景与台面",
        [
            _opt("桌面不对", "Change the tabletop material, color, or texture to better support the product."),
            _opt("墙面不对", "Adjust wall/background plane so it is cleaner and less distracting."),
            _opt("草地不对", "Make grass/outdoor surface believable and secondary to the product."),
            _opt("岩石不对", "Make rock/terrain material credible and not overpowering."),
            _opt("天空不对", "Adjust sky or horizon so it does not feel artificial or distract from the product."),
            _opt("室内环境不对", "Adjust the room environment to match the intended use case and brand tone."),
            _opt("背景太乱", "Simplify background elements and reduce visual noise."),
        ],
    ),
    _dim(
        "props",
        "图片反馈-道具元素",
        "道具元素",
        [
            _opt("道具太多", "Remove unnecessary props and keep only supporting objects that clarify use case."),
            _opt("道具太少", "Add minimal contextual props that support scale, gaming use, or lifestyle mood."),
            _opt("道具抢主体", "Reduce prop size, contrast, sharpness, or position so the product remains dominant."),
            _opt("手部姿势不自然", "Correct hand pose, grip contact, finger placement, and wrist direction so it matches the reference and looks natural."),
            _opt("包装不需要", "Remove packaging or box elements unless explicitly required."),
            _opt("配件不对", "Correct accessory type, placement, and scale; do not invent incompatible accessories."),
        ],
    ),
    _dim(
        "lighting",
        "图片反馈-灯光",
        "灯光",
        [
            _opt("太暗", "Increase exposure and product detail visibility without washing out the scene."),
            _opt("太亮", "Reduce overexposure and recover product material detail."),
            _opt("太冷", "Warm the lighting slightly while preserving brand color accuracy."),
            _opt("太暖", "Cool the lighting slightly while preserving realistic product color."),
            _opt("轮廓光不足", "Add restrained rim light or edge separation around the product."),
            _opt("阴影不自然", "Correct contact shadows and cast shadows so product placement feels real."),
            _opt("产品自发光不足", "Strengthen intended product glow with soft bloom and surface reflection, without making it look like a printed graphic."),
        ],
    ),
    _dim(
        "color_palette",
        "图片反馈-色彩",
        "色彩",
        [
            _opt("主色不对", "Adjust the dominant scene color to better match the brand and product direction."),
            _opt("辅助色不对", "Adjust accent colors so they support rather than fight the product palette."),
            _opt("饱和度过高", "Reduce saturation for a more premium product-photo look."),
            _opt("饱和度过低", "Increase color presence while keeping product colors accurate."),
            _opt("对比不够", "Increase tonal contrast and focal contrast around the product."),
            _opt("品牌色不匹配", "Align scene accents and lighting with the brand palette without recoloring the product."),
        ],
    ),
    _dim(
        "style",
        "图片反馈-风格",
        "风格",
        [
            _opt("不够真实摄影", "Move toward realistic product photography with credible lens, lighting, and materials."),
            _opt("太 C4D", "Reduce synthetic 3D-rendered look; make surfaces, lighting, and camera feel photographed."),
            _opt("太潮玩", "Reduce toy-like styling and keep the product practical and premium."),
            _opt("不够电影感", "Increase cinematic lighting, depth, and mood without sacrificing product clarity."),
            _opt("不够治愈", "Soften mood, light, and props while keeping the product clear."),
            _opt("不够科技", "Add restrained tech cues through lighting, material contrast, and composition."),
            _opt("太复杂", "Simplify scene, reduce competing ideas, and keep one clear visual concept."),
        ],
    ),
    _dim(
        "platform_fit",
        "图片反馈-平台适配",
        "平台适配",
        [
            _opt("1:1 裁切不好", "Recompose for a square feed crop with safe margins."),
            _opt("4:5 裁切不好", "Recompose for a 4:5 feed crop with safe top and bottom margins."),
            _opt("9:16 裁切不好", "Recompose for a vertical 9:16 crop while keeping the product readable."),
            _opt("封面可读性差", "Improve first-glance readability and subject hierarchy for feed preview."),
            _opt("安全边距不足", "Increase safe margins around important product and hand details."),
        ],
    ),
    _dim(
        "risk_control",
        "图片反馈-风险控制",
        "风险控制",
        [
            _opt("有文字", "Remove visible text, labels, UI words, and text overlays."),
            _opt("有水印", "Remove watermarks, signatures, and generation artifacts."),
            _opt("有竞品 logo", "Remove competitor logos or brand identifiers."),
            _opt("有未授权 IP", "Remove unauthorized characters, franchise symbols, or IP-like shapes."),
            _opt("产品被重设计", "Do not redesign or replace the product; restore the exact reference product."),
            _opt("有多余品牌标识", "Remove newly invented logos or extra brand marks not present in the product reference."),
        ],
    ),
]


LEGACY_IMAGE_FEEDBACK_OPTIONS = [
    {
        "label": "手握方向跟参考图不一致",
        "tag": "POSE_ORIENTATION_MISMATCH",
        "element": "pose_orientation",
        "instruction": "Match the reference image hand entry direction, grip angle, product orientation, and tabletop tilt more closely.",
    },
    {
        "label": "FUNLAB 发光纹路不对",
        "tag": "EMISSIVE_PATTERN_MISSING",
        "element": "emissive_pattern",
        "instruction": "Render the FUNLAB controller pattern as restrained embedded luminous linework integrated into the shell, with soft glow and bloom. Do not make it look like flat printed surface graphics or non-emissive decoration.",
    },
    {
        "label": "产品不像/产品变形",
        "tag": "PRODUCT_FIDELITY",
        "element": "product_fidelity",
        "instruction": "Preserve the exact product silhouette, proportions, colors, materials, buttons, interfaces, texture, and visible markings from the product reference image.",
    },
    {
        "label": "按键/接口/图案不对",
        "tag": "PRODUCT_DETAIL_MISMATCH",
        "element": "product_details",
        "instruction": "Correct button layout, interface edge, product graphics, and visible markings to match the product reference image.",
    },
    {
        "label": "构图不对",
        "tag": "COMPOSITION",
        "element": "composition",
        "instruction": "Adjust subject position, subject scale, whitespace, and crop safety while preserving the approved visual direction.",
    },
    {
        "label": "镜头角度不对",
        "tag": "CAMERA_ANGLE",
        "element": "camera_angle",
        "instruction": "Adjust the camera angle, focal length feel, and product viewing angle.",
    },
    {
        "label": "场景不对",
        "tag": "SCENE",
        "element": "scene",
        "instruction": "Change the surrounding scene while preserving the product reference image as the source of truth.",
    },
    {
        "label": "背景/台面不对",
        "tag": "BACKGROUND_SURFACE",
        "element": "background_surface",
        "instruction": "Adjust the background and surface material, keeping the product as the clear focal point.",
    },
    {
        "label": "道具太多/抢主体",
        "tag": "PROPS_CLUTTER",
        "element": "props",
        "instruction": "Reduce props and foreground clutter so the product remains dominant.",
    },
    {
        "label": "灯光不高级",
        "tag": "LIGHTING",
        "element": "lighting",
        "instruction": "Improve light direction, softness, contrast, rim light, product glow, and contact shadows.",
    },
    {
        "label": "色调不符合品牌",
        "tag": "COLOR_PALETTE",
        "element": "color_palette",
        "instruction": "Adjust dominant and accent colors to match the brand palette without overpowering the product.",
    },
    {
        "label": "风格不符合品牌",
        "tag": "STYLE",
        "element": "style",
        "instruction": "Adjust the visual style while preserving the approved product and content direction.",
    },
    {
        "label": "平台比例/裁切不适合",
        "tag": "PLATFORM_CROP",
        "element": "platform_crop",
        "instruction": "Adjust aspect ratio, crop safety, and platform composition.",
    },
    {
        "label": "文字/logo/IP风险",
        "tag": "RISK_CONTROL",
        "element": "risk_control",
        "instruction": "Remove text overlays, watermarks, new logos, unauthorized IP symbols, and competitor branding.",
    },
]

IMAGE_FEEDBACK_OPTIONS = LEGACY_IMAGE_FEEDBACK_OPTIONS
_OPTION_BY_TAG = {option["tag"]: option for option in LEGACY_IMAGE_FEEDBACK_OPTIONS}
_DIMENSION_BY_TOKEN = {
    token: dimension
    for dimension in IMAGE_FEEDBACK_DIMENSIONS
    for token in {dimension["key"], dimension["field_name"], dimension["label"], dimension["input_name"]}
}

FUNLAB_HARD_RULES = [
    {
        "tag": "FUNLAB_HIDDEN_EMISSIVE_PATTERN",
        "element": "brand_hard_rule_funlab_hidden_emissive_pattern",
        "label": "FUNLAB 隐藏式发光纹路",
        "instruction": (
            "For FUNLAB controller visuals, the teal shell contour pattern must always render as restrained hidden or embedded luminous linework "
            "integrated into the shell, with soft cyan-teal emission, subtle bloom, and edge reflection. It must not look like flat printed ink, "
            "stickers, or non-emissive decoration."
        ),
        "avoid": ["flat printed FUNLAB pattern", "sticker-like teal graphics", "non-emissive shell pattern"],
    }
]


DEFAULT_AVOID = ["text overlay", "watermark", "new logo", "unauthorized IP character", "competitor branding", "product redesign"]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        raw = text_value(value)
        if raw and raw not in seen:
            seen.add(raw)
            output.append(raw)
    return output


def _is_no_change(value: str) -> bool:
    return text_value(value) in NO_CHANGE_VALUES


def _is_funlab(fields: dict[str, Any]) -> bool:
    brand = select_value(fields.get("品牌")).upper()
    if brand == "FUNLAB":
        return True
    product_text = " ".join(
        [
            text_value(fields.get("产品名")),
            text_value(fields.get("内容标题")),
            text_value(fields.get("品牌型号/SKU")),
        ]
    ).upper()
    return "FUNLAB" in product_text or product_text.startswith("FF")


def brand_hard_rules_for_fields(fields: dict[str, Any]) -> list[dict[str, Any]]:
    if _is_funlab(fields):
        return FUNLAB_HARD_RULES
    return []


def normalize_feedback_dimensions(feedback_dimensions: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in (feedback_dimensions or {}).items():
        dimension = _DIMENSION_BY_TOKEN.get(text_value(raw_key))
        if not dimension:
            continue
        value = text_value(raw_value)
        if not value:
            continue
        normalized[dimension["key"]] = value
    return normalized


def _dimension_option(dimension: dict[str, Any], value: str) -> dict[str, str]:
    selected = text_value(value)
    for option in dimension["options"]:
        if selected in {option["value"], option["label"]}:
            return option
    return {
        "value": selected,
        "label": selected,
        "instruction": f"Adjust {dimension['label']} according to operator selection: {selected}.",
    }


def _next_regen_version(fields: dict[str, Any]) -> int:
    raw = fields.get("重生版本号")
    try:
        return int(raw or 0) + 1
    except (TypeError, ValueError):
        return 1


def build_image_feedback_patch(
    *,
    fields: dict[str, Any],
    feedback_text: str = "",
    feedback_dimensions: dict[str, str] | None = None,
    feedback_tags: list[str] | None = None,
    keep: list[str] | None = None,
    change: list[dict[str, str]] | None = None,
    avoid: list[str] | None = None,
) -> dict[str, Any]:
    dimensions = normalize_feedback_dimensions(feedback_dimensions)
    tags = _dedupe(feedback_tags or [])
    change_items = list(change or [])
    feedback_labels: list[str] = []
    for dimension in IMAGE_FEEDBACK_DIMENSIONS:
        selected = dimensions.get(dimension["key"], "")
        if _is_no_change(selected):
            continue
        option = _dimension_option(dimension, selected)
        feedback_labels.append(f"{dimension['label']}={option['value']}")
        change_items.append(
            {
                "element": dimension["key"],
                "dimension": dimension["label"],
                "value": option["value"],
                "instruction": option["instruction"],
            }
        )
    for tag in tags:
        option = _OPTION_BY_TAG.get(tag)
        if option:
            change_items.append({"element": option["element"], "instruction": option["instruction"]})
    if feedback_text.strip():
        change_items.append({"element": "operator_feedback", "instruction": feedback_text.strip()})

    brand_hard_rules = brand_hard_rules_for_fields(fields)
    hard_rule_avoid: list[str] = []
    for rule in brand_hard_rules:
        change_items.append(
            {
                "element": rule["element"],
                "source": "brand_hard_rule",
                "instruction": rule["instruction"],
            }
        )
        hard_rule_avoid.extend(rule.get("avoid", []))

    patch = {
        "keep": _dedupe(keep or ["approved composition", "approved scene", "approved lighting unless explicitly changed"]),
        "change": change_items,
        "avoid": _dedupe((avoid or []) + DEFAULT_AVOID + hard_rule_avoid),
        "feedback_dimensions": dimensions,
        "feedback_tags": _dedupe(feedback_labels + tags),
        "feedback_text": feedback_text.strip(),
        "brand_hard_rules": brand_hard_rules,
    }
    return patch


def image_feedback_update_fields(
    fields: dict[str, Any],
    *,
    feedback_text: str,
    feedback_dimensions: dict[str, str],
    patch: dict[str, Any],
) -> dict[str, Any]:
    version = _next_regen_version(fields)
    normalized_dimensions = normalize_feedback_dimensions(feedback_dimensions)
    updates = {
        "图片反馈标签": "; ".join(patch.get("feedback_tags", [])),
        "图片修改意见": feedback_text.strip(),
        "图片重生Patch": json.dumps(patch, ensure_ascii=False, sort_keys=True),
        "重生版本号": version,
        "本轮必须保留": "\n".join(patch.get("keep", [])),
        "本轮只改什么": "\n".join(f"{item.get('element')}: {item.get('instruction')}" for item in patch.get("change", [])),
        "图片生成状态": "待生成",
        "图片任务record_id": "",
        "生成图片file_token": "",
        "FB Staged Photo ID": "",
    }
    for dimension in IMAGE_FEEDBACK_DIMENSIONS:
        updates[dimension["field_name"]] = normalized_dimensions.get(dimension["key"]) or "不改"
    return updates


def normalize_copy_overrides(copy_overrides: dict[str, str] | None) -> dict[str, str]:
    raw = copy_overrides or {}
    caption = text_value(
        raw.get("caption_en")
        or raw.get("caption_en_override")
        or raw.get("caption")
        or raw.get("Caption EN")
    )
    hashtags = text_value(
        raw.get("hashtag_en")
        or raw.get("hashtag_en_override")
        or raw.get("hashtags_en")
        or raw.get("hashtags")
        or raw.get("Hashtag EN")
    )
    updates: dict[str, str] = {}
    if caption:
        updates["Caption EN"] = caption
    if hashtags:
        updates["Hashtag EN"] = hashtags
    return updates


def copy_override_update_fields(
    copy_overrides: dict[str, str] | None,
    *,
    feedback_text: str,
) -> dict[str, Any]:
    updates: dict[str, Any] = normalize_copy_overrides(copy_overrides)
    if not updates:
        return {}
    updates["文案人工锁定"] = True
    updates["文案修改意见"] = feedback_text.strip() or "运营卡片直接修改文案"
    return updates


def approval_update_fields(
    action: str,
    fields: dict[str, Any],
    *,
    feedback_text: str = "",
    copy_overrides: dict[str, str] | None = None,
    feedback_dimensions: dict[str, str] | None = None,
    feedback_tags: list[str] | None = None,
    keep: list[str] | None = None,
    change: list[dict[str, str]] | None = None,
    avoid: list[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if action == "approve_schedule":
        return {"状态": "待发布", "审批通过": True, "审批通过时间": now, "最终素材确认": True}
    if action == "reject":
        return {"状态": "已驳回", "图片修改意见": feedback_text.strip()}
    if action in {"regenerate_image", "regenerate_both"}:
        patch = build_image_feedback_patch(
            fields=fields,
            feedback_text=feedback_text,
            feedback_dimensions=feedback_dimensions or {},
            feedback_tags=feedback_tags or [],
            keep=keep or [],
            change=change or [],
            avoid=avoid or [],
        )
        updates = image_feedback_update_fields(
            fields,
            feedback_text=feedback_text,
            feedback_dimensions=feedback_dimensions or {},
            patch=patch,
        )
        if action == "regenerate_both":
            copy_updates = copy_override_update_fields(copy_overrides, feedback_text=feedback_text)
            if copy_updates:
                updates.update(copy_updates)
            else:
                updates.update({"AI生成状态": "待生成", "文案修改意见": feedback_text.strip()})
        return updates
    if action == "regenerate_copy":
        copy_updates = copy_override_update_fields(copy_overrides, feedback_text=feedback_text)
        if copy_updates:
            copy_updates["图片Prompt人工锁定"] = True
            return copy_updates
        return {"AI生成状态": "待生成", "文案修改意见": feedback_text.strip(), "图片Prompt人工锁定": True}
    raise ValueError(f"unsupported approval action: {action}")


def approval_card_preview(record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "brand": select_value(fields.get("品牌")),
        "platform": fields.get("平台"),
        "product": text_value(fields.get("产品名")) or text_value(fields.get("内容标题")),
        "sku": text_value(fields.get("品牌型号/SKU")),
        "test_point": select_value(fields.get("实验变量")),
        "image_status": select_value(fields.get("图片生成状态")),
        "caption": text_value(fields.get("Caption EN")),
        "hashtags": text_value(fields.get("Hashtag EN")),
        "feedback_schema_version": "v3-12-dimensions",
        "feedback_dimensions": IMAGE_FEEDBACK_DIMENSIONS,
        "feedback_options": [],
        "brand_hard_rules": brand_hard_rules_for_fields(fields),
        "actions": ["approve_schedule", "regenerate_image", "regenerate_copy", "regenerate_both", "reject"],
    }
