"""飞书引用、白名单和租户校验，不连接真实平台。"""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from goofish_bridge.feishu_adapter import build_customer_card, parse_operator_event


def event():
    return {"header": {"app_id": "app", "tenant_key": "tenant", "event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_id": {"open_id": "owner"}, "sender_type": "user", "tenant_key": "tenant"},
                      "message": {"chat_type": "p2p", "message_type": "text", "message_id": "reply",
                                  "chat_id": "chat", "content": '{"text":" 原文 "}',
                                  "create_time": "1900000000000", "parent_id": "original", "root_id": "root"}}}


def test_preserves_text_and_direct_parent_only():
    parsed = parse_operator_event(event(), "app", "owner")
    assert parsed["text"] == " 原文 "
    assert parsed["parent_id"] == "original"
    raw = event()
    raw["event"]["message"].pop("parent_id")
    assert parse_operator_event(raw, "app", "owner")["parent_id"] == ""


@pytest.mark.parametrize("path,value", [
    (("header", "app_id"), "other"),
    (("header", "tenant_key"), "other"),
    (("event", "sender", "sender_type"), "app"),
    (("event", "sender", "sender_id", "open_id"), "stranger"),
    (("event", "message", "chat_type"), "group"),
    (("event", "message", "message_type"), "image"),
    (("event", "message", "content"), "not-json"),
    (("event", "message", "create_time"), None),
])
def test_unauthorized_or_invalid_events_rejected(path, value):
    raw = copy.deepcopy(event())
    node = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert parse_operator_event(raw, "app", "owner") is None


def test_binding_restricts_chat():
    binding = {"app_id": "app", "tenant_key": "tenant", "open_id": "owner", "chat_id": "other-chat"}
    assert parse_operator_event(event(), "app", "owner", binding) is None


@pytest.mark.parametrize("key", ["img-test", "", None, 42, " " * 3, "a" * 513])
def test_image_event_resource_validation(key):
    raw = event()
    raw["event"]["message"].update(message_type="image", content=json.dumps({"image_key": key}))
    parsed = parse_operator_event(raw, "app", "owner")
    if key == "img-test":
        assert parsed["message_type"] == "image"
        assert parsed["image_key"] == key
        assert parsed["parent_id"] == "original"
        assert parsed["text"] == "[图片]"
    else:
        assert parsed is None


@pytest.mark.asyncio
async def test_sdk_receiver_is_collected_before_disconnect(monkeypatch):
    import lark_oapi as lark

    from goofish_bridge import feishu_adapter

    class FakeClient:
        def __init__(self, *args, event_handler, **kwargs):
            self.handler = event_handler
            self.reader = None

        async def _connect(self):
            self.reader = asyncio.create_task(asyncio.Event().wait())
            asyncio.get_running_loop().call_soon(
                self.handler._do_without_validation, json.dumps({"schema": "2.0", **event()}).encode(),
            )

        async def _ping_loop(self):
            await asyncio.Event().wait()

        async def _disconnect(self):
            assert self.reader.done(), "正常关闭前必须先取消并回收 SDK 接收任务"

    monkeypatch.setattr(lark.ws, "Client", FakeClient)
    monkeypatch.setattr(feishu_adapter, "credentials", lambda _: ("app", "secret", "owner"))
    monkeypatch.setattr(feishu_adapter, "configure_sdk_direct", lambda: SimpleNamespace(close=lambda: None))
    rows = await feishu_adapter.probe_events(None, SimpleNamespace(event=lambda *args: None), seconds=1)
    assert rows[0]["parent_id"] == "original"


def test_success_without_message_id_remains_unknown():
    from goofish_bridge.feishu_adapter import FeishuRuntime

    runtime = FeishuRuntime.__new__(FeishuRuntime)
    runtime.binding = {"chat_id": "chat"}
    response = SimpleNamespace(success=lambda: True, data=None, code=0)
    runtime.api = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(create=lambda _: response))))
    state, message_id, _ = runtime.send({"delivery_id": "delivery", "text": "测试"})
    assert state == "UNKNOWN"
    assert message_id is None


def test_customer_card_hides_routing_identifiers_and_contains_reply_input():
    card = build_customer_card({"account_name": "AI编程哥", "customer_name": "客户甲",
                                "entries": [{"speaker": "客户", "text": "请问还有货？"}]})
    content = json.dumps(card, ensure_ascii=False)
    assert "AI编程哥" in content
    assert "请问还有货？" in content
    assert "reply_text" in content
    assert "查看更多" in content
    assert "uid" not in content.lower()
    assert "card_key" not in content


def test_customer_card_default_view_uses_latest_four_entries():
    entries = [{"speaker": "客户", "text": f"消息{i}"} for i in range(1, 7)]
    content = json.dumps(build_customer_card({"entries": entries}), ensure_ascii=False)
    assert "消息1" not in content
    assert "消息2" not in content
    assert all(f"消息{i}" in content for i in range(3, 7))


@pytest.mark.parametrize("outcome,expected", [
    ("success", "SERVER_ACCEPTED"), ("missing_id", "UNKNOWN"),
    ("rejected", "FAILED"), ("timeout", "UNKNOWN"),
])
def test_customer_reminder_uses_quote_api_and_never_patches_card(outcome, expected):
    from goofish_bridge.feishu_adapter import FeishuRuntime

    calls = []

    def reply(request):
        calls.append(request)
        if outcome == "timeout":
            raise TimeoutError("模拟网络结果未知")
        return SimpleNamespace(success=lambda: outcome != "rejected", code=230011,
                               msg="测试拒绝", data=SimpleNamespace(
                                   message_id=None if outcome == "missing_id" else "new-reminder"))

    def unexpected(_):
        pytest.fail("提醒不得更新原卡片或降级为重复建卡")

    runtime = FeishuRuntime.__new__(FeishuRuntime)
    runtime.binding = {"chat_id": "owner-chat"}
    runtime.api = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(reply=reply, create=unexpected, patch=unexpected))))
    text = "闲鱼账号：北美草原狼\n客户昵称：张先生\n新消息：还有货吗？"
    state, message_id, _ = runtime.send({"kind": "CUSTOMER_REMINDER", "delivery_id": "stable-uuid",
                                       "target_message_id": "original-card", "text": text})
    assert state == expected
    assert message_id == ("new-reminder" if outcome == "success" else None)
    assert len(calls) == 1
    assert calls[0].message_id == "original-card"
    assert calls[0].request_body.msg_type == "text"
    assert json.loads(calls[0].request_body.content) == {"text": text}
    assert calls[0].request_body.uuid == "stable-uuid"
    assert calls[0].request_body.reply_in_thread is False


def test_customer_image_is_uploaded_and_replied_to_original_card(monkeypatch):
    from goofish_bridge import media
    from goofish_bridge.feishu_adapter import FeishuRuntime

    requests = []

    def upload(request):
        requests.append(("upload", request))
        assert request.request_body.image_type == "message"
        assert request.request_body.image.name == "customer.jpg"
        assert request.request_body.image.read() == b"image-bytes"
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(image_key="image-key"))

    def reply_image(request):
        requests.append(("reply", request))
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="image-message"))

    monkeypatch.setattr(media, "download_customer_image", lambda _: b"image-bytes")
    monkeypatch.setattr(media, "inspect_image", lambda _: ("jpg", "image/jpeg", 12, 20))
    runtime = FeishuRuntime.__new__(FeishuRuntime)
    runtime.binding = {"chat_id": "owner-chat"}
    runtime.api = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        image=SimpleNamespace(create=upload), message=SimpleNamespace(reply=reply_image))))
    state, message_id, error = runtime.send({"kind": "CUSTOMER_IMAGE", "delivery_id": "fixed-id",
                                                  "target_message_id": "original-card",
                                                  "text": "https://img.alicdn.com/test.jpg"})
    assert (state, message_id, error) == ("SERVER_ACCEPTED", "image-message", "")
    assert [kind for kind, _ in requests] == ["upload", "reply"]
    sent = requests[1][1]
    assert sent.message_id == "original-card"
    assert sent.request_body.msg_type == "image"
    assert json.loads(sent.request_body.content) == {"image_key": "image-key"}
    assert sent.request_body.uuid == "fixed-id"


def test_customer_image_missing_resource_scope_is_actionable(monkeypatch):
    from goofish_bridge import media
    from goofish_bridge.feishu_adapter import FeishuRuntime

    monkeypatch.setattr(media, "download_customer_image", lambda _: b"image-bytes")
    monkeypatch.setattr(media, "inspect_image", lambda _: ("jpg", "image/jpeg", 12, 20))
    runtime = FeishuRuntime.__new__(FeishuRuntime)
    runtime.binding = {"chat_id": "owner-chat"}
    runtime.api = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        image=SimpleNamespace(create=lambda _: SimpleNamespace(
            success=lambda: False, code=99991672)))))
    state, message_id, error = runtime.send({"kind": "CUSTOMER_IMAGE", "delivery_id": "fixed-id",
                                                  "target_message_id": "original-card",
                                                  "text": "https://img.alicdn.com/test.jpg"})
    assert state == "FAILED"
    assert message_id is None
    assert "im:resource" in error
