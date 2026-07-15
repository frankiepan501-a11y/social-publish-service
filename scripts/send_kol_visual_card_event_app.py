from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


BJ = timezone(timedelta(hours=8))
DEFAULT_SERVICE_URL = os.environ.get("SOCIAL_PUBLISH_SERVICE_URL", "https://fb-ig-social-publish.zeabur.app")
DEFAULT_SEO_CHAT_ID = "oc_4ddd938ddb73201ed7354337eb2226ac"
DEFAULT_OUT = (
    r"C:\Users\Administrator\codex-company-os\outputs\fb_ig_ops"
    r"\kol_visual_card_event_app_send_20260706.json"
)


def _json_request(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {raw[:800]}") from exc


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


def service_headers() -> dict[str, str]:
    token = os.environ.get("SOCIAL_PUBLISH_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def build_visual_payload(*, week_start: str, write_back: bool, post_url: str, thumbnail_url: str) -> dict:
    return {
        "week_start": week_start,
        "source": "manual",
        "write_back": write_back,
        "prepare_image_keys": True,
        "min_score": 70,
        "strategies": [
            {
                "fields": {
                    "账号": "FUNLAB Instagram",
                    "品牌": "FUNLAB",
                    "平台": "Instagram",
                    "本周主推产品池": "FUNLAB FF01A-04 Controller",
                    "本周业务重点": "按钮回调链路验证；不触发 Meta 发布。",
                    "内容支柱配比": "场景图/产品图/UGC 参考",
                    "目标信号优先级": "保存/互动/点击",
                    "状态": "启用",
                }
            }
        ],
        "posts": [
            {
                "brand": "FUNLAB",
                "account_name": "Callback Test Visual Reference",
                "account_url": "https://www.instagram.com/",
                "post_url": post_url,
                "thumbnail_url": thumbnail_url,
                "followers": "测试",
                "country": "US",
                "language": "EN",
                "visual_tags": ["controller", "hands", "desk setup", "product slot", "image post"],
                "borrow": "按钮回调验证用：卡片由聪哥分身3号发送，点击应进入 Event Hub；画面字段只作测试，不作为正式参考对象。",
                "avoid": "不复制原图人物、屏幕内容、第三方 logo、原文案或水印；测试记录如入库需后续清理。",
            }
        ],
    }


def send_card(token: str, *, chat_id: str, card: dict) -> dict:
    return _json_request(
        "POST",
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--chat-id", default=DEFAULT_SEO_CHAT_ID)
    parser.add_argument("--week-start", default="2026-07-06")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--post-url", default="https://www.instagram.com/p/DUOY0y9Dqfd/")
    parser.add_argument("--thumbnail-url", default="https://picsum.photos/seed/fbig-callback-test-gamepad/900/600.jpg")
    parser.add_argument("--write-back-candidate", action="store_true")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    service_url = args.service_url.rstrip("/")
    payload = build_visual_payload(
        week_start=args.week_start,
        write_back=args.write_back_candidate,
        post_url=args.post_url,
        thumbnail_url=args.thumbnail_url,
    )
    card_resp = _json_request(
        "POST",
        f"{service_url}/discovery/kol/visual-posts",
        payload,
        headers=service_headers(),
    )
    cards = card_resp.get("feishu_cards") or []
    if not cards:
        raise RuntimeError(f"service returned no feishu_cards: {card_resp}")
    card = cards[0]
    card["header"]["title"]["content"] = "[测试确认] [SEO/P2] FUNLAB KOL 图片帖参考候选（按钮回调修复）"

    token = None
    send_resp = {"code": None, "msg": "preview only"}
    if args.send:
        token = tenant_token()
        send_resp = send_card(token, chat_id=args.chat_id, card=card)
        if send_resp.get("code") != 0:
            raise RuntimeError(f"send card failed: {send_resp}")

    evidence = {
        "sent_at": datetime.now(BJ).isoformat(),
        "sent": bool(args.send),
        "sender_app": "FEISHU_EVENT_APP_ID",
        "target_chat_id": args.chat_id,
        "service_url": service_url,
        "write_back_candidate": bool(args.write_back_candidate),
        "service_status": card_resp.get("status"),
        "candidate_count": len(card_resp.get("candidates") or []),
        "created_count": len(card_resp.get("created") or []),
        "created_record_ids": [
            ((item.get("record") or {}).get("record_id") or item.get("record_id"))
            for item in (card_resp.get("created") or [])
        ],
        "image_key_errors": card_resp.get("image_key_errors") or [],
        "send_code": send_resp.get("code"),
        "message_id": ((send_resp.get("data") or {}).get("message_id") if isinstance(send_resp, dict) else ""),
        "send_response_sender": (((send_resp.get("data") or {}).get("sender") or {}) if isinstance(send_resp, dict) else {}),
        "card_title": card.get("header", {}).get("title", {}).get("content"),
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
                "sender": evidence["send_response_sender"],
                "created_record_ids": evidence["created_record_ids"],
                "out": str(out),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
