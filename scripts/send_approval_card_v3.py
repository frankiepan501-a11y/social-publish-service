from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import mimetypes
import os
from pathlib import Path
import sys
import uuid
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.approval import IMAGE_FEEDBACK_DIMENSIONS  # noqa: E402


BJ = timezone(timedelta(hours=8))
FRANKIE_UNION_ID = "on_6e85dd60606f76f2d5af892785ac1dfe"
CONTENT_BASE_TOKEN = "JXw5bUmRoaaCPqsbc6HctWfknhe"
CONTENT_TABLE_ID = "tblhVnKqqhTXvO3Y"
CARD_SCHEMA_VERSION = "v3-12-dimensions-copy"
DEFAULT_RECORD_ID = "recvok0GfDEPx0"
DEFAULT_CAPTION_EN = (
    "A low-light setup built around quiet focus. The controller stays exact, "
    "the scene changes around it, and the glow does the talking."
)
DEFAULT_HASHTAG_EN = "#FUNLAB #HiddenGlow #GamingSetup #ControllerDesign #DeskSetup #SwitchController"
DEFAULT_DESIGN_REFERENCE_IMAGE = (
    r"C:\Users\Administrator\codex-company-os\outputs\fb_ig_ops"
    r"\design_reference_inputs\gamepad_handheld_darkdesk_design_reference.png"
)
DEFAULT_GENERATED_IMAGE = (
    r"C:\Users\Administrator\codex-company-os\outputs\fb_ig_ops"
    r"\approval_feedback_regen\funlab_ff01a_04_card_feedback_regen_v1.png"
)
DEFAULT_OUT = (
    r"C:\Users\Administrator\codex-company-os\outputs\fb_ig_ops"
    r"\approval_card_v3_wholepost_send_20260703.json"
)


def _json_request(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {raw[:600]}") from exc


def tenant_token() -> str:
    app_id = os.environ.get("FEISHU_EVENT_APP_ID")
    app_secret = os.environ.get("FEISHU_EVENT_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_EVENT_APP_ID/FEISHU_EVENT_APP_SECRET are required")
    resp = _json_request(
        "POST",
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    token = resp.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"tenant token missing: {resp}")
    return token


def upload_image(token: str, image_path: Path) -> str:
    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    mime = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    boundary = f"----fbigv3{uuid.uuid4().hex}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="image_type"\r\n\r\n')
    body.extend(b"message\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(image_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/images",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"image upload failed HTTP {exc.code}: {raw[:600]}") from exc
    if data.get("code") != 0:
        raise RuntimeError(f"image upload failed: {data}")
    image_key = (data.get("data") or {}).get("image_key")
    if not image_key:
        raise RuntimeError(f"image_key missing: {data}")
    return image_key


def record_url(record_id: str) -> str:
    return (
        f"https://u1wpma3xuhr.feishu.cn/base/{CONTENT_BASE_TOKEN}"
        f"?table={CONTENT_TABLE_ID}&record={record_id}"
    )


def md(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def plain(content: str) -> dict:
    return {"tag": "plain_text", "content": content}


def button(
    action_name: str,
    label: str,
    action: str,
    record_id: str,
    *,
    original_caption_en: str,
    original_hashtag_en: str,
    primary: bool = False,
    danger: bool = False,
) -> dict:
    return {
        "tag": "button",
        "action_type": "form_submit",
        "name": action_name,
        "text": plain(label),
        "type": "danger" if danger else ("primary" if primary else "default"),
        "value": {
            "action": "fbig_image_feedback",
            "service_action": action,
            "record_id": record_id,
            "card_schema_version": CARD_SCHEMA_VERSION,
            "source": "approval_card_v3",
            "original_caption_en": original_caption_en,
            "original_hashtag_en": original_hashtag_en,
        },
    }


def select_element(dim: dict) -> dict:
    options = [
        {"text": plain(option["value"]), "value": option["value"]}
        for option in dim.get("options", [])
    ]
    return {
        "tag": "select_static",
        "name": dim["input_name"],
        "placeholder": plain(f"{dim['label']}：不改"),
        "options": options,
    }


def build_card(
    *,
    record_id: str,
    product: str,
    caption_en: str,
    hashtag_en: str,
    design_reference_key: str | None,
    generated_key: str | None,
) -> dict:
    form_elements = [
        {
            "tag": "input",
            "name": "caption_en_override",
            "label": plain("Caption EN"),
            "default_value": caption_en,
            "input_type": "multiline_text",
            "placeholder": plain("可直接改英文帖文；不改则保持原文"),
        },
        {
            "tag": "input",
            "name": "hashtag_en_override",
            "label": plain("Hashtag EN"),
            "default_value": hashtag_en,
            "input_type": "multiline_text",
            "placeholder": plain("可直接改 hashtags；不改则保持原文"),
        },
    ]
    form_elements.extend(select_element(dim) for dim in IMAGE_FEEDBACK_DIMENSIONS)
    form_elements.extend(
        [
            {
                "tag": "input",
                "name": "feedback_text",
                "label": plain("补充意见"),
                "placeholder": plain("例如：手握方向按参考图；发光纹路要像嵌入外壳的柔和光线"),
            },
            button(
                "fbig_approve_schedule",
                "通过排期",
                "approve_schedule",
                record_id,
                original_caption_en=caption_en,
                original_hashtag_en=hashtag_en,
                primary=True,
            ),
            button(
                "fbig_regenerate_image",
                "重生图片",
                "regenerate_image",
                record_id,
                original_caption_en=caption_en,
                original_hashtag_en=hashtag_en,
            ),
            button(
                "fbig_regenerate_copy",
                "重生文案",
                "regenerate_copy",
                record_id,
                original_caption_en=caption_en,
                original_hashtag_en=hashtag_en,
            ),
            button(
                "fbig_regenerate_both",
                "图文都重生",
                "regenerate_both",
                record_id,
                original_caption_en=caption_en,
                original_hashtag_en=hashtag_en,
            ),
            button(
                "fbig_reject",
                "驳回不发",
                "reject",
                record_id,
                original_caption_en=caption_en,
                original_hashtag_en=hashtag_en,
                danger=True,
            ),
        ]
    )

    intro_lines = [
        f"**产品**：{product}",
        f"**记录**：[打开内容卡片]({record_url(record_id)})",
        "**处理边界**：通过排期只写入待发布队列；不会直接发布到 Meta。",
    ]
    if design_reference_key:
        intro_lines.append("**比对规则**：上方参考图展示设计/竞品参考，用于判断构图、手握方向、氛围和借鉴距离。")
    intro_lines.extend(
        [
            "**产品保真**：产品原图已作为生图附件绑定，用于约束产品外形、按钮、材质和纹路。",
            "**品牌硬规则**：FUNLAB 隐藏式发光纹路自动注入，不作为人工问题选项。",
        ]
    )
    elements: list[dict] = [
        md("\n".join(intro_lines)),
        {"tag": "hr"},
    ]
    if design_reference_key:
        elements.append(md("**设计参考图（竞品/博主参考）**"))
        elements.append({"tag": "img", "img_key": design_reference_key, "alt": plain("设计参考图")})
    if generated_key:
        elements.append(md("**当前生成图**"))
        elements.append({"tag": "img", "img_key": generated_key, "alt": plain("当前生成图")})
    elements.extend(
        [
            {"tag": "hr"},
            md(
                "\n".join(
                    [
                        "**帖子文案确认**",
                        f"**Caption EN**\n{caption_en}",
                        f"**Hashtag EN**\n{hashtag_en}",
                    ]
                )
            ),
            {"tag": "hr"},
            md("文案可在输入框内直接修改；图片按维度选择需要改的项，没问题的维度保持不选即可。"),
            {
                "tag": "form",
                "name": "fbig_v3_feedback_form",
                "elements": form_elements,
            },
        ]
    )
    return {
        "config": {"wide_screen_mode": True, "enable_forward": False},
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": f"🟡 [SEO·P2] FB/IG 素材审批 · {product}"},
        },
        "elements": elements,
    }


def send_card(token: str, union_id: str, card: dict) -> dict:
    return _json_request(
        "POST",
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=union_id",
        {
            "receive_id": union_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        token=token,
    )


def send_card_to(token: str, receive_id: str, receive_id_type: str, card: dict) -> dict:
    return _json_request(
        "POST",
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        token=token,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-id", default=DEFAULT_RECORD_ID)
    parser.add_argument("--product", default="FUNLAB FF01A-04 Controller")
    parser.add_argument("--caption-en", default=DEFAULT_CAPTION_EN)
    parser.add_argument("--hashtag-en", default=DEFAULT_HASHTAG_EN)
    parser.add_argument("--design-reference-image", "--reference-image", default=DEFAULT_DESIGN_REFERENCE_IMAGE)
    parser.add_argument("--generated-image", default=DEFAULT_GENERATED_IMAGE)
    parser.add_argument("--union-id", default=FRANKIE_UNION_ID)
    parser.add_argument("--receive-id", default="")
    parser.add_argument("--receive-id-type", default="union_id", choices=["union_id", "open_id", "chat_id"])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    token = None
    design_reference_key = None
    generated_key = None
    if args.send:
        token = tenant_token()
        if args.design_reference_image:
            design_path = Path(args.design_reference_image)
            if design_path.is_file():
                design_reference_key = upload_image(token, design_path)
        if args.generated_image:
            generated_path = Path(args.generated_image)
            if generated_path.is_file():
                generated_key = upload_image(token, generated_path)

    card = build_card(
        record_id=args.record_id,
        product=args.product,
        caption_en=args.caption_en,
        hashtag_en=args.hashtag_en,
        design_reference_key=design_reference_key,
        generated_key=generated_key,
    )
    result = {"code": None, "msg": "preview only"}
    if args.send:
        assert token is not None
        receive_id = args.receive_id or args.union_id
        result = send_card_to(token, receive_id, args.receive_id_type, card)
        if result.get("code") != 0:
            raise RuntimeError(f"send card failed: {result}")

    evidence = {
        "sent_at": datetime.now(BJ).isoformat(),
        "card_schema_version": CARD_SCHEMA_VERSION,
        "record_id": args.record_id,
        "product": args.product,
        "caption_en": args.caption_en,
        "hashtag_en": args.hashtag_en,
        "record_url": record_url(args.record_id),
        "sent": bool(args.send),
        "send_code": result.get("code"),
        "message_id": ((result.get("data") or {}).get("message_id") if isinstance(result, dict) else ""),
        "design_reference_image": str(Path(args.design_reference_image)),
        "design_reference_image_uploaded": bool(design_reference_key),
        "generated_image_uploaded": bool(generated_key),
        "receive_id_type": args.receive_id_type,
        "dimension_count": len(IMAGE_FEEDBACK_DIMENSIONS),
        "button_actions": [
            "approve_schedule",
            "regenerate_image",
            "regenerate_copy",
            "regenerate_both",
            "reject",
        ],
        "render_fix": "form_submit_buttons_are_direct_form_children",
        "display_fix": "show_design_reference_and_whole_post_copy_inputs",
        "copy_input_names": ["caption_en_override", "hashtag_en_override"],
        "card": card,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "sent": evidence["sent"],
                "message_id": evidence["message_id"],
                "dimension_count": evidence["dimension_count"],
                "out": str(out),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
