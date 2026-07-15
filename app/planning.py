from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from .rules import multi_value, select_value, text_value


PLAN_VERSION = "fb-ig-plan-v1.0"

DEFAULT_PILLARS = ["产品场景", "卖点教育", "UGC/KOL社证", "品牌幕后", "活动权益"]
DEFAULT_SIGNALS = ["Saves", "Shares", "Comments", "Profile Visits", "Link Clicks"]
DEFAULT_EXPERIMENTS = ["Hook", "Visual", "CTA", "Proof", "Format"]
DAILY_ACTIVE_STATUSES = {"", "待日审", "已推送", "重推参考", "重推选题"}
CONTENT_CALENDAR_PILLAR_ALIASES = {"UGC": "UGC/KOL社证"}


def _fields(record_or_fields: dict[str, Any]) -> dict[str, Any]:
    if "fields" in record_or_fields and isinstance(record_or_fields.get("fields"), dict):
        return record_or_fields["fields"]
    return record_or_fields


def _record_id(record_or_fields: dict[str, Any]) -> str:
    return text_value(record_or_fields.get("record_id") or record_or_fields.get("id"))


def _split_pool(raw: Any) -> list[str]:
    value = text_value(raw)
    if not value:
        return []
    parts = re.split(r"[\n\r,，;；|/]+", value)
    return [part.strip() for part in parts if part.strip()]


def _first_pool(raw: Any, default: str) -> str:
    values = _split_pool(raw)
    return values[0] if values else default


def _content_calendar_pillar(raw: Any) -> str:
    pillar = select_value(raw) or text_value(raw)
    return CONTENT_CALENDAR_PILLAR_ALIASES.get(pillar, pillar)


def _date_value(raw: Any) -> date | None:
    if raw in ("", None):
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).date()
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    value = str(raw).strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _week_start(raw: str | None, now: str | None = None) -> date:
    parsed = _date_value(raw)
    if parsed:
        return parsed - timedelta(days=parsed.weekday())
    parsed_now = _date_value(now) or datetime.now(timezone.utc).date()
    return parsed_now - timedelta(days=parsed_now.weekday())


def plan_input_hash(fields: dict[str, Any]) -> str:
    keys = [
        "周次",
        "计划发布账号",
        "品牌",
        "平台",
        "内容支柱",
        "产品名",
        "参考对象链接",
        "SEO主关键词",
        "GEO目标问题",
        "搜索意图",
        "Hashtag词组池",
    ]
    raw = json.dumps({key: fields.get(key) for key in keys}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_strategies_from_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strategies: list[dict[str, Any]] = []
    for account in accounts:
        fields = _fields(account)
        account_name = text_value(fields.get("账号名称"))
        if not account_name:
            continue
        brand = select_value(fields.get("品牌")) or ("FUNLAB" if "funlab" in account_name.lower() else "Powkong")
        platform = select_value(fields.get("平台")) or ("Facebook" if "fb" in account_name.lower() else "Instagram")
        weekly_limit = fields.get("每周上限") or fields.get("周频次") or 3
        strategies.append(
            {
                "策略标题": f"{account_name} 默认策略",
                "账号名称": account_name,
                "品牌": brand,
                "平台": platform,
                "每周候选数": weekly_limit,
                "内容支柱配比": "\n".join(DEFAULT_PILLARS),
                "目标信号优先级": "\n".join(DEFAULT_SIGNALS),
                "产品池": fields.get("默认产品池") or fields.get("产品池") or "",
                "SEO主题簇": "",
                "GEO问题池": "",
                "Hashtag分层池": "",
                "参考对象策略": "产品保真优先；只借鉴场景、构图、灯光和叙事，不复制竞品产品外观。",
                "禁用词": "",
                "状态": "启用",
            }
        )
    return strategies


def _weekly_count(fields: dict[str, Any]) -> int:
    raw = fields.get("每周候选数") or fields.get("周频次") or fields.get("每周上限") or 3
    try:
        count = int(float(raw))
    except (TypeError, ValueError):
        count = 3
    return max(1, min(count, 7))


def _brand_tag(brand: str) -> str:
    return "#FUNLAB" if brand.upper() == "FUNLAB" else "#Powkong"


def _category_tags(product: str) -> list[str]:
    lower = product.lower()
    if "dock" in lower or "底座" in product:
        return ["#SwitchDock", "#GamingDock"]
    if "controller" in lower or "手柄" in product:
        return ["#GamingController", "#ControllerSetup"]
    if "case" in lower or "包" in product:
        return ["#SwitchCase", "#TravelSetup"]
    return ["#SwitchAccessories", "#GamingSetup"]


def _compose_hashtags(brand: str, product: str, pool: Any, intent: str) -> str:
    tags: list[str] = [_brand_tag(brand)]
    for item in _split_pool(pool):
        tag = "#" + re.sub(r"[^0-9A-Za-z_]", "", item.lstrip("#")).strip("_")
        if tag != "#":
            tags.append(tag[:41])
    tags.extend(_category_tags(product))
    if intent == "教程型":
        tags.extend(["#SetupTips", "#HowTo"])
    elif intent == "对比型":
        tags.extend(["#GamingGear", "#SetupUpgrade"])
    elif intent == "购买型":
        tags.extend(["#GameRoom", "#DeskSetup"])
    else:
        tags.extend(["#DeskSetup", "#SetupInspo", "#GameRoom"])

    clean: list[str] = []
    seen: set[str] = set()
    blocked = ("8bitdo", "gamesir", "razer", "nintendo", "pokemon", "mario", "zelda")
    for tag in tags:
        normalized = tag.lower()
        if normalized in seen or any(term in normalized for term in blocked):
            continue
        clean.append(tag)
        seen.add(normalized)
        if len(clean) >= 8:
            break
    while len(clean) < 5:
        fallback = ["#GamingSetup", "#ControllerSetup", "#DeskSetup", "#SetupInspo", "#GameRoom"][len(clean) % 5]
        if fallback.lower() not in seen:
            clean.append(fallback)
            seen.add(fallback.lower())
    return " ".join(clean[:8])


def _default_keyword(product: str, brand: str) -> str:
    if product:
        return f"{product} setup"
    return "gaming setup accessories" if brand.upper() != "FUNLAB" else "hidden glow gaming controller"


def _default_question(product: str, keyword: str) -> str:
    subject = product or keyword or "gaming accessories"
    return f"What should I look for when choosing {subject}?"


def _strategy_values(fields: dict[str, Any], idx: int, product: str, brand: str) -> dict[str, str]:
    pillars = _split_pool(fields.get("内容支柱配比")) or DEFAULT_PILLARS
    signals = _split_pool(fields.get("目标信号优先级")) or DEFAULT_SIGNALS
    seo_topics = _split_pool(fields.get("SEO主题簇"))
    geo_questions = _split_pool(fields.get("GEO问题池"))
    pillar = pillars[idx % len(pillars)]
    signal = signals[idx % len(signals)]
    experiment = DEFAULT_EXPERIMENTS[idx % len(DEFAULT_EXPERIMENTS)]
    keyword = seo_topics[idx % len(seo_topics)] if seo_topics else _default_keyword(product, brand)
    question = geo_questions[idx % len(geo_questions)] if geo_questions else _default_question(product, keyword)
    intent = ["信息型", "灵感型", "教程型", "对比型", "购买型"][idx % 5]
    return {
        "pillar": pillar,
        "signal": signal,
        "experiment": experiment,
        "keyword": keyword,
        "question": question,
        "intent": intent,
    }


def _strategy_week(fields: dict[str, Any], week_label: str) -> str:
    return text_value(fields.get("周次") or fields.get("策略周次")) or week_label


def _is_operator_strategy(fields: dict[str, Any]) -> bool:
    return select_value(fields.get("提交状态")) == "运营已提交" or select_value(fields.get("策略来源")) == "operator"


def _is_default_strategy(fields: dict[str, Any]) -> bool:
    return select_value(fields.get("提交状态")) == "默认锁定" or select_value(fields.get("策略来源")) == "default"


def _reference_matches(ref_fields: dict[str, Any], brand: str, platform: str, pillar: str, product: str) -> bool:
    status = select_value(ref_fields.get("状态"))
    if status and status != "可用":
        return False
    ref_brand = select_value(ref_fields.get("品牌"))
    if ref_brand and ref_brand.upper() != brand.upper():
        return False
    ref_platform = select_value(ref_fields.get("平台"))
    if ref_platform and ref_platform != platform:
        return False
    ref_pillar = select_value(ref_fields.get("内容支柱"))
    if ref_pillar and ref_pillar != pillar:
        return False
    scope = text_value(ref_fields.get("适用品类/产品")).lower()
    if scope and product and product.lower() not in scope and not any(token.lower() in scope for token in _category_tags(product)):
        return False
    return True


def _pick_reference(
    references: list[dict[str, Any]], *, brand: str, platform: str, pillar: str, product: str, offset: int = 0
) -> dict[str, str]:
    matched = [
        ref
        for ref in references
        if _reference_matches(_fields(ref), brand=brand, platform=platform, pillar=pillar, product=product)
    ]
    if not matched:
        return {
            "参考对象": "待补充参考对象",
            "参考对象链接": "",
            "参考理由": "参考对象库暂无匹配项；本候选可先进入选题池，但日确认前应补设计/竞品参考。",
            "借鉴元素": "",
            "禁止复制元素": "不复制竞品产品外观、logo、文案、未授权角色或品牌标识。",
        }
    ref = matched[offset % len(matched)]
    fields = _fields(ref)
    title = text_value(fields.get("参考标题")) or text_value(fields.get("账号/帖子URL")) or _record_id(ref)
    return {
        "参考对象": title,
        "参考对象链接": text_value(fields.get("账号/帖子URL")),
        "参考理由": f"匹配 {brand}/{platform}/{pillar}，用于借鉴场景、构图、灯光或叙事节奏。",
        "借鉴元素": text_value(fields.get("可借鉴元素")),
        "禁止复制元素": text_value(fields.get("禁止复制元素"))
        or "不复制竞品产品外观、logo、文案、未授权角色或品牌标识。",
    }


def build_weekly_candidates(
    strategies: list[dict[str, Any]],
    references: list[dict[str, Any]],
    *,
    week_start: str | None = None,
    now: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    start = _week_start(week_start, now)
    week_label = start.isoformat()
    candidates: list[dict[str, Any]] = []
    operator_strategy_keys = {
        (_strategy_week(_fields(strategy), week_label), text_value(_fields(strategy).get("账号名称") or _fields(strategy).get("账号")))
        for strategy in strategies
        if _is_operator_strategy(_fields(strategy))
    }
    for s_idx, strategy in enumerate(strategies):
        fields = _fields(strategy)
        if select_value(fields.get("状态")) == "暂停":
            continue
        strategy_week = _strategy_week(fields, week_label)
        if strategy_week and strategy_week != week_label:
            continue
        account = text_value(fields.get("账号名称") or fields.get("账号"))
        if not account:
            continue
        if _is_default_strategy(fields) and (strategy_week, account) in operator_strategy_keys:
            continue
        brand = select_value(fields.get("品牌")) or ("FUNLAB" if "funlab" in account.lower() else "Powkong")
        platform = select_value(fields.get("平台")) or ("Facebook" if "fb" in account.lower() else "Instagram")
        products = [product for product in _split_pool(fields.get("产品池")) if "hero product" not in product.lower()]
        if not products:
            continue
        count = _weekly_count(fields)
        for i in range(count):
            product = products[i % len(products)]
            values = _strategy_values(fields, i, product, brand)
            planned_date = start + timedelta(days=(s_idx + i) % 7)
            ref = _pick_reference(
                references,
                brand=brand,
                platform=platform,
                pillar=values["pillar"],
                product=product,
                offset=i,
            )
            hashtags = _compose_hashtags(brand, product, fields.get("Hashtag分层池"), values["intent"])
            title = f"{product}｜{values['pillar']}｜{values['keyword']}"
            candidate = {
                "候选标题": title,
                "周次": week_label,
                "计划日期": planned_date.isoformat(),
                "计划发布账号": account,
                "品牌": brand,
                "平台": platform,
                "发布位置": "IG Feed" if platform == "Instagram" else "FB Page",
                "内容支柱": values["pillar"],
                "目标信号": values["signal"],
                "实验变量": values["experiment"],
                "产品名": product,
                "产品池命中": product,
                **ref,
                "SEO主关键词": values["keyword"],
                "GEO目标问题": values["question"],
                "搜索意图": values["intent"],
                "语义实体词": ", ".join([brand, product, values["pillar"], values["signal"]]),
                "长尾关键词": f"{values['keyword']} for {values['intent']}",
                "目标落地页": "",
                "Hashtag词组池": hashtags,
                "SEO/GEO生成说明": (
                    f"首句优先自然承接 `{values['keyword']}` 或 `{values['question']}`；"
                    "hashtag 从品牌、品类、场景、意图、社区五层选择，禁止竞品词和未授权 IP。"
                ),
                "日确认状态": "待日审",
                "重推次数": 0,
            }
            candidate["运行/回放ID"] = "planhash-" + plan_input_hash(candidate)[:12]
            candidates.append(candidate)
            if len(candidates) >= limit:
                return candidates
    return candidates


def select_daily_candidates(candidates: list[dict[str, Any]], *, target_date: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    target = _date_value(target_date) or datetime.now(timezone.utc).date()
    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        fields = _fields(candidate)
        status = select_value(fields.get("日确认状态"))
        planned = _date_value(fields.get("计划日期"))
        if planned != target or status not in DAILY_ACTIVE_STATUSES:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def build_daily_confirm_card(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = _fields(candidate)
    reference_image_key = _first_daily_reference_image_key(fields)
    return {
        "title": f"[SEO/P2] FB/IG 当日选题确认 · {fields.get('计划发布账号', '')}",
        "candidate_record_id": _record_id(candidate),
        "summary": {
            "计划日期": text_value(fields.get("计划日期")),
            "账号": text_value(fields.get("计划发布账号")),
            "品牌": select_value(fields.get("品牌")) or text_value(fields.get("品牌")),
            "平台": select_value(fields.get("平台")) or text_value(fields.get("平台")),
            "发布位置": text_value(fields.get("发布位置")),
            "选题": text_value(fields.get("候选标题")),
            "产品": text_value(fields.get("产品名")),
            "内容支柱": select_value(fields.get("内容支柱")),
            "目标信号": text_value(fields.get("目标信号")),
            "SEO主关键词": text_value(fields.get("SEO主关键词")),
            "GEO目标问题": text_value(fields.get("GEO目标问题")),
            "参考对象": text_value(fields.get("参考对象")),
            "参考对象链接": text_value(fields.get("参考对象链接")),
            "借鉴元素": text_value(fields.get("借鉴元素")),
            "禁止复制元素": text_value(fields.get("禁止复制元素")),
            "参考图Key": reference_image_key,
            "Caption/Hashtag方向": text_value(fields.get("SEO/GEO生成说明")),
        },
        "actions": [
            {"label": "确认生成", "action": "confirm_generate"},
            {"label": "重推选题", "action": "reselect_topic"},
            {"label": "重推参考对象", "action": "reselect_reference"},
            {"label": "跳过今天/改期", "action": "skip_or_reschedule"},
        ],
    }


def _card_plain(content: Any) -> dict[str, str]:
    return {"tag": "plain_text", "content": text_value(content)}


def _card_md(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _card_img(img_key: str, alt: str) -> dict[str, Any]:
    return {"tag": "img", "img_key": img_key, "alt": _card_plain(alt)}


def _card_field(label: str, value: Any) -> dict[str, Any]:
    text = text_value(value) or "未填写"
    return {"is_short": True, "text": {"tag": "lark_md", "content": f"**{label}**\n{text}"}}


def _action_button(label: str, value: dict[str, Any], *, primary: bool = False, danger: bool = False) -> dict[str, Any]:
    button_type = "primary" if primary else ("danger" if danger else "default")
    return {"tag": "button", "text": _card_plain(label), "type": button_type, "value": value}


def _first_daily_reference_image_key(fields: dict[str, Any]) -> str:
    for key in (
        "设计参考图Key",
        "设计参考图image_key",
        "参考图Key",
        "参考图image_key",
        "样例帖子1图片Key",
        "样例帖子2图片Key",
    ):
        value = text_value(fields.get(key))
        if value:
            return value
    return ""


def build_daily_confirm_feishu_card(card: dict[str, Any], *, run_id: str = "") -> dict[str, Any]:
    summary = card.get("summary") if isinstance(card.get("summary"), dict) else {}
    candidate_record_id = text_value(card.get("candidate_record_id"))
    base_value = {
        "action": "fbig_daily_confirm",
        "candidate_record_id": candidate_record_id,
        "run_id": text_value(run_id),
        "source": "daily_confirm_card_v1",
    }
    fields = [
        _card_field("账号", summary.get("账号")),
        _card_field("日期", summary.get("计划日期")),
        _card_field("品牌/平台", " / ".join([item for item in [text_value(summary.get("品牌")), text_value(summary.get("平台"))] if item])),
        _card_field("产品", summary.get("产品")),
        _card_field("内容支柱", summary.get("内容支柱")),
        _card_field("目标信号", summary.get("目标信号")),
        _card_field("SEO主关键词", summary.get("SEO主关键词")),
        _card_field("GEO目标问题", summary.get("GEO目标问题")),
    ]
    reference_link = text_value(summary.get("参考对象链接"))
    reference_title = text_value(summary.get("参考对象")) or "待补充参考对象"
    reference_md = f"**参考对象**：{reference_title}"
    if reference_link:
        reference_md = f"**参考对象**：[{reference_title}]({reference_link})"
    elements: list[dict[str, Any]] = [
        _card_md(
            "\n".join(
                [
                    "**这张卡用来做什么**：确认今天这条 FB/IG 选题是否进入 Caption/Hashtag + 图片生成链路；确认后仍只进入审批流程，不会直接发布到 Meta。",
                    "**运营只需要判断**：选题、产品、SEO/GEO 方向、参考对象和借鉴边界是否合适。",
                    "**不合适时怎么处理**：优先点 `重推参考对象`，如果选题方向本身不对再点 `重推选题`。",
                ]
            )
        ),
        {"tag": "div", "fields": fields},
        {"tag": "hr"},
        _card_md(
            "\n".join(
                [
                    f"**选题**：{text_value(summary.get('选题')) or '未填写'}",
                    reference_md,
                    f"**借鉴元素**：{text_value(summary.get('借鉴元素')) or '未填写'}",
                    f"**禁止复制元素**：{text_value(summary.get('禁止复制元素')) or '不复制竞品产品外观、logo、文案、未授权角色或品牌标识。'}",
                    f"**Caption/Hashtag 方向**：{text_value(summary.get('Caption/Hashtag方向')) or '未填写'}",
                ]
            )
        ),
    ]
    reference_image_key = text_value(summary.get("参考图Key"))
    if reference_image_key:
        elements.append(_card_img(reference_image_key, "设计参考图预览"))
    else:
        elements.append(_card_md("**参考图预览**：未内嵌图片。日确认仍可先确认选题；正式生图前应补设计参考图或可复用 IG/FB 图片帖。"))
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    _action_button("确认生成", {**base_value, "plan_action": "confirm_generate"}, primary=True),
                    _action_button("重推参考对象", {**base_value, "plan_action": "reselect_reference"}),
                    _action_button("重推选题", {**base_value, "plan_action": "reselect_topic"}),
                    _action_button("跳过/改期", {**base_value, "plan_action": "skip_or_reschedule"}, danger=True),
                ],
            },
        ]
    )
    return {
        "config": {"wide_screen_mode": True, "enable_forward": False},
        "header": {"template": "yellow", "title": {"tag": "plain_text", "content": text_value(card.get("title"))}},
        "elements": elements,
    }


def content_calendar_fields_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    fields = _fields(candidate)
    platform = select_value(fields.get("平台")) or text_value(fields.get("平台"))
    target_signal = _split_pool(fields.get("目标信号")) or [text_value(fields.get("目标信号")) or "Reach"]
    slots = _split_pool(fields.get("发布位置")) or [text_value(fields.get("发布位置")) or "IG Feed"]
    return {
        "状态": "选题中",
        "内容标题": text_value(fields.get("候选标题")),
        "产品名": text_value(fields.get("产品名")),
        "品牌": select_value(fields.get("品牌")) or text_value(fields.get("品牌")),
        "平台": [platform] if platform else [],
        "发布位置": slots,
        "计划发布时间": text_value(fields.get("计划日期")),
        "内容支柱": _content_calendar_pillar(fields.get("内容支柱")),
        "目标信号": target_signal,
        "实验变量": select_value(fields.get("实验变量")) or text_value(fields.get("实验变量")),
        "图片生成模式": "Codex Image",
        "AI生成状态": "待生成",
        "选题来源": "周候选池",
        "参考对象": text_value(fields.get("参考对象")),
        "参考对象链接": text_value(fields.get("参考对象链接")),
        "参考理由": text_value(fields.get("参考理由")),
        "借鉴元素": text_value(fields.get("借鉴元素")),
        "禁止复制元素": text_value(fields.get("禁止复制元素")),
        "SEO主关键词": text_value(fields.get("SEO主关键词")),
        "GEO目标问题": text_value(fields.get("GEO目标问题")),
        "搜索意图": select_value(fields.get("搜索意图")) or text_value(fields.get("搜索意图")),
        "语义实体词": text_value(fields.get("语义实体词")),
        "长尾关键词": text_value(fields.get("长尾关键词")),
        "目标落地页": text_value(fields.get("目标落地页")),
        "Hashtag词组池": text_value(fields.get("Hashtag词组池")),
        "SEO/GEO生成说明": text_value(fields.get("SEO/GEO生成说明")),
        "日确认状态": "已确认",
        "日确认动作": "确认生成",
        "日确认时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "重推次数": fields.get("重推次数") or 0,
        "周候选record_id": _record_id(candidate),
    }


def apply_plan_action(
    candidate: dict[str, Any],
    *,
    action: str,
    references: list[dict[str, Any]] | None = None,
    reschedule_date: str | None = None,
) -> dict[str, Any]:
    fields = dict(_fields(candidate))
    current_retry = int(float(fields.get("重推次数") or 0))
    references = references or []
    updates: dict[str, Any] = {"重推次数": current_retry}
    content_fields: dict[str, Any] | None = None

    if action == "confirm_generate":
        updates.update(
            {
                "日确认状态": "已确认",
                "日确认动作": "确认生成",
                "日确认时间": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        content_fields = content_calendar_fields_from_candidate(candidate)
    elif action == "reselect_reference":
        brand = select_value(fields.get("品牌")) or text_value(fields.get("品牌"))
        platform = select_value(fields.get("平台")) or text_value(fields.get("平台"))
        pillar = select_value(fields.get("内容支柱")) or text_value(fields.get("内容支柱"))
        product = text_value(fields.get("产品名"))
        new_ref = _pick_reference(references, brand=brand, platform=platform, pillar=pillar, product=product, offset=current_retry + 1)
        updates.update(new_ref)
        updates.update({"日确认状态": "重推参考", "日确认动作": "重推参考对象", "重推次数": current_retry + 1})
    elif action == "reselect_topic":
        brand = select_value(fields.get("品牌")) or text_value(fields.get("品牌"))
        product = text_value(fields.get("产品名"))
        idx = current_retry + 1
        pillar = DEFAULT_PILLARS[idx % len(DEFAULT_PILLARS)]
        experiment = DEFAULT_EXPERIMENTS[idx % len(DEFAULT_EXPERIMENTS)]
        keyword = _default_keyword(product, brand)
        question = _default_question(product, keyword)
        updates.update(
            {
                "候选标题": f"{product}｜{pillar}｜{keyword}",
                "内容支柱": pillar,
                "实验变量": experiment,
                "SEO主关键词": keyword,
                "GEO目标问题": question,
                "日确认状态": "重推选题",
                "日确认动作": "重推选题",
                "重推次数": current_retry + 1,
            }
        )
    elif action == "skip_or_reschedule":
        updates.update({"日确认状态": "改期" if reschedule_date else "跳过", "日确认动作": "跳过今天/改期"})
        if reschedule_date:
            updates["计划日期"] = reschedule_date
    else:
        raise ValueError(f"Unsupported plan action: {action}")

    return {"updates": updates, "content_fields": content_fields}
