"""仅根据已实测字段规范化消息，未知结构保留异常，不猜路由。"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from goofish_bridge.goofish_adapter import normalize_id


def _event(key, uid, cid, customer, source, source_time, text, nickname, kind, timezone, offline=False):
    now = time.time()
    valid = True
    try:
        cid, customer = normalize_id(cid), normalize_id(customer)
        if not isinstance(source, str) or not source:
            raise ValueError("缺少可信源消息 ID")
        timestamp = int(source_time) / 1000
        if not 946684800 < timestamp <= now + 86400:
            raise ValueError("消息时间异常")
    except (ValueError, TypeError):
        timestamp, valid = None, False
    if not isinstance(text, str):
        text, kind, valid = "", "unsupported", False
    if not isinstance(nickname, str):
        nickname = "昵称未知"
    received = datetime.fromtimestamp(timestamp or now, UTC).astimezone(ZoneInfo(timezone))
    return dict(account_key=key, account_uid=uid, cid=cid if isinstance(cid, str) else "",
                customer_uid=customer if isinstance(customer, str) else "",
                source_message_id=source if isinstance(source, str) else None,
                source_time=timestamp, received_at=now, text=text or "收到暂不支持解析的消息，请到闲鱼查看。",
                customer_name=nickname or "昵称未知", message_type=kind,
                parse_state="OK" if valid else "ERROR", offline=offline,
                display_time=("消息时间：" if timestamp else "收到时间：") + received.strftime("%Y-%m-%d %H:%M:%S"))


def from_push(decoded, key, uid, timezone="Asia/Shanghai"):
    one = decoded.get("1")
    if not isinstance(one, dict) or not isinstance(one.get("10"), dict):
        return None
    ext = one["10"]
    sender = ext.get("senderUserId", "")
    identity = (one.get("1") or {}).get("1", "") if isinstance(one.get("1"), dict) else ""
    item = _event(key, uid, one.get("2", ""), sender, one.get("3"), one.get("5"),
                  ext.get("reminderContent", ""), ext.get("reminderTitle", ""),
                  "text_or_summary", timezone)
    if identity and (not isinstance(identity, str) or identity.removesuffix("@goofish") != sender):
        item["parse_state"] = "ERROR"
        item["text"] = "消息发送者字段不一致，请到闲鱼核对。"
    return item


def from_history(model, key, uid, timezone="Asia/Shanghai", offline=True):
    message = model.get("message") or {}
    ext = message.get("extension") or {}
    content = message.get("content") or {}
    text, kind = "", "unsupported"
    try:
        custom = content.get("custom") or {}
        if custom.get("data"):
            payload = json.loads(base64.b64decode(custom["data"], validate=True))
            if payload.get("contentType") == 1:
                text = (payload.get("text") or {}).get("text", "")
                kind = "text"
            elif payload.get("contentType") == 2:
                text, kind = "客户发送了一张图片，请到闲鱼查看。", "image"
        elif content.get("contentType") == 1:
            text, kind = (content.get("text") or {}).get("text", ""), "text"
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        text = "收到暂不支持解析的消息，请到闲鱼查看。"
    return _event(key, uid, message.get("cid", ""), ext.get("senderUserId", ""),
                  message.get("messageId"), message.get("createAt"), text,
                  ext.get("reminderTitle", ""), kind, timezone, offline)
