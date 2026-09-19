"""固定路由、双向去重、跨账号和崩溃恢复的业务测试。"""

import json

import pytest

from goofish_bridge.router import route_card_operator, route_operator
from goofish_bridge.store import Store
from tests.test_bridge_feishu_adapter import event as feishu_event


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "bridge.sqlite")
    instance.bind_account("A1", "uid-a", "账号一", now=100)
    instance.bind_account("A2", "uid-b", "账号二", now=100)
    yield instance
    instance.close()


def incoming(key="A1", uid="uid-a", source="msg1", buyer="buyer", cid="chat", text="相同正文"):
    return dict(account_key=key, account_uid=uid, source_message_id=source, customer_uid=buyer,
                cid=cid, text=text, source_time=200, received_at=201, parse_state="OK", message_type="text")


def forward(store, item, message_id="feishu-original"):
    inbox = store.ingest(item)
    outbox = store.claim_outbox()
    store.finish_outbox(outbox["delivery_id"], "SERVER_ACCEPTED", message_id)
    return inbox


def reply(message_id="reply1", parent="feishu-original", text=" 原样回复 ", created=300):
    return dict(message_id=message_id, parent_id=parent, text=text, create_time=created * 1000)


def test_inbox_atomic_dedup_but_same_text_is_not_identity(store):
    assert store.ingest(incoming())
    assert store.ingest(incoming()) is None
    assert store.ingest(incoming(source="msg2"))
    assert store.db.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM feishu_outbox").fetchone()[0] == 2


def test_feishu_inbox_display_hides_internal_ids_and_shows_account_name(store):
    store.bind_account("A1", "uid-a", "北美草原狼", now=100)
    forward(store, incoming(text="请问还有货吗？"), "forward-display")
    text = store.db.execute("SELECT text FROM feishu_outbox WHERE feishu_message_id=?",
                            ("forward-display",)).fetchone()[0]
    assert "北美草原狼" in text
    assert "请问还有货吗？" in text
    assert "uid-a" not in text
    assert "buyer" not in text
    assert "chat" not in text
    assert "inbox" not in text


def test_routine_states_are_not_sent_to_feishu(store):
    store.state("A1", "STARTING")
    store.state("A1", "ONLINE")
    assert store.db.execute("SELECT count(*) FROM feishu_outbox").fetchone()[0] == 0
    store.state("A1", "RISK_PAUSED", "RiskControlError")
    text = store.db.execute("SELECT text FROM feishu_outbox").fetchone()[0]
    assert text == "账号一：风控暂停（RiskControlError）"


def test_same_customer_two_accounts_never_cross_routes(store):
    forward(store, incoming(), "forward-a")
    forward(store, incoming("A2", "uid-b"), "forward-b")
    task_a = store.receive_reply(reply("reply-a", "forward-a"), now=300)
    task_b = store.receive_reply(reply("reply-b", "forward-b"), now=300)
    assert (task_a["account_key"], task_a["account_uid"]) == ("A1", "uid-a")
    assert (task_b["account_key"], task_b["account_uid"]) == ("A2", "uid-b")
    assert task_a["text"] == " 原样回复 "


def test_feishu_reply_receipt_only_contains_body_and_final_status(store):
    forward(store, incoming(), "forward-receipt")
    task = store.receive_reply(reply("reply-receipt", "forward-receipt", "有货的"), now=300)
    store.state("A1", "ONLINE")
    task = store.claim_reply("A1", "request-receipt", "uuid-receipt", now=300)
    store.finish_reply(task["task_id"], "SERVER_ACCEPTED", "server-msg")
    assert store.db.execute("SELECT count(*) FROM feishu_outbox WHERE kind='RECEIPT'").fetchone()[0] == 0
    transcript = store.db.execute("SELECT transcript FROM feishu_cards").fetchone()[0]
    assert '"speaker": "我方"' in transcript
    assert '"text": "有货的"' in transcript
    assert '"status": "发送成功"' in transcript
    assert "uid-a" not in transcript
    assert "buyer" not in transcript


def test_card_reply_uses_card_message_without_quote(store):
    forward(store, incoming(text="客户问题"), "card-message")
    task = store.receive_card_reply({"message_id": "card-action-1", "card_message_id": "card-message",
                                     "text": "卡片回复", "event_id": "card-action-1"}, now=300)
    assert task["card_key"]
    store.state("A1", "ONLINE")
    claimed = store.claim_reply("A1", "card-mid", "card-uuid", now=300)
    store.finish_reply(claimed["task_id"], "SERVER_ACCEPTED", "server-reply")
    transcript = store.db.execute("SELECT transcript FROM feishu_cards").fetchone()[0]
    assert '"text": "卡片回复"' in transcript
    assert '"status": "发送成功"' in transcript


def test_card_action_routes_only_bound_operator(store):
    forward(store, incoming(text="卡片客户问题"), "card-route-message")
    raw = {"header": {"app_id": "app", "tenant_key": "tenant", "event_type": "card.action.trigger",
                       "event_id": "card-event-1"},
           "event": {"operator": {"open_id": "owner", "tenant_key": "tenant"},
                     "context": {"open_message_id": "card-route-message", "open_chat_id": "chat"},
                     "action": {"value": {"op": "reply"}, "form_value": {"reply_text": "卡片发送"}}}}
    binding = dict(app_id="app", tenant_key="tenant", open_id="owner", chat_id="chat")
    task = route_card_operator(store, raw, binding, now=300)
    assert task["text"] == "卡片发送"
    raw["event"]["operator"]["open_id"] = "intruder"
    assert route_card_operator(store, raw, binding, now=300) is None


def test_card_update_is_requeued_after_dispatching_recovery(store):
    forward(store, incoming(text="第一句"), "card-recovery")
    store.ingest(incoming(source="card-recovery-2", text="第二句"))
    update = store.claim_outbox()
    assert update["kind"] == "CUSTOMER_CARD_UPDATE"
    store.recover()
    queued = store.db.execute("""SELECT kind,state FROM feishu_outbox
      WHERE card_key IS NOT NULL ORDER BY created_at DESC LIMIT 1""").fetchone()
    assert queued["kind"] == "CUSTOMER_CARD_UPDATE"
    assert queued["state"] == "QUEUED"


def test_multiple_card_updates_reuse_message_id_without_unique_conflict(store):
    forward(store, incoming(text="第一句"), "card-update-1")
    store.ingest(incoming(source="card-update-2", text="第二句"))
    first = store.claim_outbox()
    store.finish_outbox(first["delivery_id"], "SERVER_ACCEPTED", "card-message")
    store.ingest(incoming(source="card-update-3", text="第三句"))
    second = store.claim_outbox()
    store.finish_outbox(second["delivery_id"], "SERVER_ACCEPTED", "card-message")
    assert store.db.execute("SELECT count(*) FROM feishu_outbox WHERE kind='CUSTOMER_CARD_UPDATE' AND state='SERVER_ACCEPTED'").fetchone()[0] == 2


def test_card_view_toggle_limits_default_entries_and_expands_on_request(store):
    forward(store, incoming(text="一"), "card-view-1")
    for index in range(2, 8):
        store.ingest(incoming(source=f"card-view-{index}", text=str(index)))
    payload = store.db.execute("SELECT text FROM feishu_outbox WHERE kind='CUSTOMER_CARD_UPDATE' ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()[0]
    assert len(json.loads(payload)["entries"]) == 4
    store.set_card_view("card-view-1", True)
    payload = store.db.execute("SELECT text FROM feishu_outbox WHERE kind='CUSTOMER_CARD_UPDATE' ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()[0]
    assert len(json.loads(payload)["entries"]) == 7


def test_reply_repeated_event_creates_only_one_task(store):
    forward(store, incoming())
    one = store.receive_reply(reply(), now=300)
    two = store.receive_reply(reply(), now=300)
    assert one["task_id"] == two["task_id"]
    assert store.db.execute("SELECT count(*) FROM reply_tasks").fetchone()[0] == 1


def test_delayed_event_expires_from_original_time(store):
    forward(store, incoming())
    task = store.receive_reply(reply(created=200), now=900)
    assert task["state"] == "EXPIRED"


def test_restart_dispatching_never_resends(store):
    forward(store, incoming())
    store.receive_reply(reply(), now=300)
    store.state("A1", "ONLINE")
    task = store.claim_reply("A1", "mid", "uuid", now=300)
    assert task["state"] == "DISPATCHING"
    store.recover()
    assert store.task(task["task_id"])["state"] == "UNKNOWN"
    store.state("A1", "ONLINE")
    assert store.claim_reply("A1", "new", "new", now=400) is None


def test_same_account_serialized_and_rate_limited(store):
    forward(store, incoming())
    store.receive_reply(reply("r1"), now=300)
    store.receive_reply(reply("r2"), now=300)
    store.state("A1", "ONLINE")
    first = store.claim_reply("A1", "mid1", "uuid1", now=300)
    assert store.claim_reply("A1", "mid2", "uuid2", now=361) is None
    store.finish_reply(first["task_id"], "SERVER_ACCEPTED", "server-msg")
    assert store.claim_reply("A1", "mid2", "uuid2", now=350) is None
    assert store.claim_reply("A1", "mid2", "uuid2", now=361)


def test_invalid_reference_does_not_create_task(store):
    store.notice("notice", "状态提示")
    delivery = store.claim_outbox()
    store.finish_outbox(delivery["delivery_id"], "SERVER_ACCEPTED", "notice-id")
    assert store.receive_reply(reply(parent="notice-id"), now=300) is None
    assert store.db.execute("SELECT count(*) FROM reply_tasks").fetchone()[0] == 0


def test_identity_change_is_rejected(store):
    with pytest.raises(ValueError):
        store.bind_account("A1", "someone-else", "新昵称")
    with pytest.raises(ValueError):
        store.ingest(incoming(uid="uid-b"))


def test_baseline_and_self_messages_not_forwarded(store):
    item = incoming()
    item["source_time"] = 99
    assert store.ingest(item) is None
    assert store.ingest(incoming(buyer="uid-a")) is None


def test_referenced_status_is_customer_text(store):
    forward(store, incoming(), "original")
    raw = feishu_event()
    raw["event"]["message"]["content"] = '{"text":"状态"}'
    raw["event"]["message"]["create_time"] = "300000"
    binding = dict(app_id="app", tenant_key="tenant", open_id="owner", chat_id="chat")
    result = route_operator(store, raw, binding, now=300)
    assert result["text"] == "状态"


def test_untrusted_actor_cannot_enqueue(store):
    forward(store, incoming(), "original")
    raw = feishu_event()
    raw["event"]["sender"]["sender_id"]["open_id"] = "intruder"
    binding = dict(app_id="app", tenant_key="tenant", open_id="owner", chat_id="chat")
    assert route_operator(store, raw, binding) is None
    assert store.db.execute("SELECT count(*) FROM reply_tasks").fetchone()[0] == 0


def test_delivery_unknown_never_claimed_again(store):
    store.notice("notice", "测试")
    store.claim_outbox()
    store.recover()
    assert store.claim_outbox() is None


def test_new_live_message_does_not_erase_unfilled_recovery_gap(store):
    store.ingest(incoming())
    store.sync_result("A1", "uid-a", "chat", True, "")
    first = store.sync_positions("A1")[0]
    item = incoming(source="later")
    item["source_time"] = 500
    store.ingest(item)
    store.sync_result("A1", "uid-a", "chat", False, "未补齐")
    next_position = store.sync_positions("A1")[0]
    assert next_position["checkpoint"] == first["watermark"]
    assert next_position["watermark"] == 500


def test_recovery_gaps_are_persisted_without_notification_flood(store):
    store.sync_result("A1", "uid-a", "chat-one", False, "历史查询被拒绝")
    store.sync_result("A1", "uid-a", "chat-two", False, "历史正文结构未知")
    assert len(store.sync_positions("A1")) == 2
    assert store.db.execute("SELECT count(*) FROM feishu_outbox WHERE idempotency_key LIKE 'gap:%'").fetchone()[0] == 0
    assert store.sync_positions("A1")[1]["gap"] == "历史正文结构未知"


def test_authorized_non_text_reply_gets_explicit_rejection(store):
    raw = feishu_event()
    raw["event"]["message"]["message_type"] = "image"
    raw["event"]["message"]["content"] = '{"image_key":"fake"}'
    binding = dict(app_id="app", tenant_key="tenant", open_id="owner", chat_id="chat")
    assert route_operator(store, raw, binding) is None
    notices = store.db.execute("SELECT text FROM feishu_outbox").fetchall()
    assert len(notices) == 1
    assert "纯文本" in notices[0][0]
