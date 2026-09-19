"""已实测协议字段与异常身份的规范化。"""

import time

from goofish_bridge.messages import from_history, from_push


def test_verified_legacy_id_and_time_are_preserved():
    stamp = str(int(time.time() * 1000))
    raw = {"1": {"1": {"1": "buyer@goofish"}, "2": "chat@goofish", "3": "source.PNM",
                 "5": stamp, "10": {"senderUserId": "buyer", "reminderContent": "你好"}}}
    parsed = from_push(raw, "A1", "account", "UTC")
    assert parsed["source_message_id"] == "source.PNM"
    assert parsed["source_time"] == int(stamp) / 1000
    assert parsed["cid"] == "chat"
    assert parsed["parse_state"] == "OK"


def test_conflicting_sender_prevents_reply():
    raw = {"1": {"1": {"1": "other@goofish"}, "2": "chat", "3": "source", "5": int(time.time() * 1000),
                 "10": {"senderUserId": "buyer", "reminderContent": "文字"}}}
    assert from_push(raw, "A1", "account", "UTC")["parse_state"] == "ERROR"


def test_unknown_metadata_does_not_get_guessed_identity():
    assert from_push({"operation": {}}, "A1", "account", "UTC") is None
    row = from_history({"message": {"extension": {"senderUserId": "buyer"}}}, "A1", "account", "UTC")
    assert row["source_message_id"] is None
    assert row["parse_state"] == "ERROR"
    assert "收到时间" in row["display_time"]


def test_malformed_text_is_preserved_as_non_replyable_anomaly():
    raw = {"1": {"1": {"1": "buyer@goofish"}, "2": "chat", "3": "source",
                 "5": int(time.time() * 1000), "10": {
                     "senderUserId": "buyer", "reminderContent": {"unexpected": "object"}}}}
    result = from_push(raw, "A1", "account", "UTC")
    assert isinstance(result["text"], str)
    assert result["parse_state"] == "ERROR"
