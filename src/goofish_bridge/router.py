"""本人直接引用路由，所有发送目标仅从持久化映射取得。"""

from goofish_bridge.feishu_adapter import operator_identity, parse_operator_event


def route_operator(store, raw, binding, ttl=600, now=None):
    event = parse_operator_event(raw, binding["app_id"], binding["open_id"], binding)
    if not event:
        identity = operator_identity(raw, binding["app_id"], binding["open_id"], binding)
        if identity:
            store.notice(f"unsupported:{identity['message_id']}", "仅支持非空文字或单张 PNG/JPG/JPEG 图片消息；当前内容未发送，请重新引用原客户消息提交。")
        return None
    if not event["parent_id"]:
        text = store.status_text() if event["text"] == "状态" else "请直接引用机器人转发的原客户消息，发送文字或单张 PNG/JPG/JPEG 图片。"
        store.notice(f"operator:{event['message_id']}", text)
        return None
    if len(event["text"]) > 2000:
        store.notice(f"operator:{event['message_id']}", "正文超过首版 2000 字符限制，未发送；请缩短后重新引用提交。")
        return None
    event["event_id"] = (raw.get("header") or {}).get("event_id", "")
    return store.receive_reply(event, ttl=ttl, now=now)


def route_card_operator(store, raw, binding, ttl=600, now=None):
    """处理卡片输入框提交，按卡片消息 ID 固定定位客户。"""
    header = raw.get("header") or {}
    event = raw.get("event") or {}
    operator = event.get("operator") or {}
    context = event.get("context") or {}
    action = event.get("action") or {}
    if (header.get("app_id") != binding["app_id"]
            or header.get("event_type") != "card.action.trigger"
            or operator.get("open_id") != binding["open_id"]
            or operator.get("tenant_key") != header.get("tenant_key")
            or context.get("open_chat_id") != binding["chat_id"]):
        return None
    value = action.get("value") or {}
    if value.get("op") in {"expand", "collapse"}:
        store.set_card_view(context.get("open_message_id", ""), value["op"] == "expand")
        return None
    form = action.get("form_value") or {}
    text = form.get("reply_text")
    if not isinstance(text, str):
        return None
    event_id = header.get("event_id") or f"card:{context.get('open_message_id')}:{action.get('name', '')}"
    return store.receive_card_reply({
        "message_id": event_id,
        "event_id": event_id,
        "card_message_id": context.get("open_message_id", ""),
        "text": text,
    }, ttl=ttl, now=now)
