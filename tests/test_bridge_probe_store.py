"""诊断脱敏和未知发送恢复。"""

import json

from goofish_bridge.probe_store import ProbeStore


def test_redaction_keeps_relationship_but_hides_values(tmp_path):
    store = ProbeStore(tmp_path)
    try:
        raw = {"sender": "private-user", "nested": {"user": "private-user", "text": "私密正文"}}
        clean = store.redact(raw)
        assert clean["sender"] == clean["nested"]["user"]
        assert "private-user" not in json.dumps(clean, ensure_ascii=False)
        assert "私密正文" not in json.dumps(clean, ensure_ascii=False)
    finally:
        store.close()


def test_restart_never_requeues_dispatching(tmp_path):
    store = ProbeStore(tmp_path)
    first = store.prepare_send("A1", "user-a", "chat", "customer", "mid", "uuid")
    other = store.prepare_send("A2", "user-b", "chat", "customer", "mid2", "uuid2")
    store.close()
    store = ProbeStore(tmp_path)
    try:
        store.recover_account("A1")
        states = dict(store.connection.execute("SELECT id,state FROM probe_sends"))
        assert states[first] == "UNKNOWN"
        assert states[other] == "DISPATCHING"
    finally:
        store.close()


def test_export_excludes_send_targets(tmp_path):
    store = ProbeStore(tmp_path)
    try:
        store.prepare_send("A1", "private-uid", "chat", "buyer", "mid", "uuid")
        store.event("goofish", {"body": "secret"})
        target = tmp_path / "sample.jsonl"
        store.export(target)
        assert "private-uid" not in target.read_text(encoding="utf-8")
        assert "secret" not in target.read_text(encoding="utf-8")
    finally:
        store.close()


def test_redaction_also_hides_identifiers_in_dynamic_keys(tmp_path):
    store = ProbeStore(tmp_path)
    try:
        value = {"squadId_123456789": "private", "unexpected-customer-name": "value"}
        clean = json.dumps(store.redact(value), ensure_ascii=False)
        assert "123456789" not in clean
        assert "unexpected-customer-name" not in clean
    finally:
        store.close()
