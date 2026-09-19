"""跨进程入站身份检查与数据库提交确认。"""

import queue

import pytest

from goofish_bridge.store import Store
from goofish_bridge.supervisor import handle_worker_event
from tests.test_bridge_store import incoming


def test_ipc_ack_only_after_persist(tmp_path):
    store = Store(tmp_path / "bridge.sqlite")
    try:
        store.bind_account("A1", "uid-a", "账号一", now=100)
        channel = queue.Queue()
        item = dict(id="receipt", kind="incoming", account="A1", uid="uid-a", payload=incoming(), durable=True)
        handle_worker_event(store, item, {"A1": channel})
        assert store.db.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 1
        assert channel.get_nowait() == {"kind": "committed", "id": "receipt"}
    finally:
        store.close()


def test_payload_cannot_impersonate_another_account(tmp_path):
    store = Store(tmp_path / "bridge.sqlite")
    try:
        store.bind_account("A1", "uid-a", "账号一", now=100)
        store.bind_account("A2", "uid-b", "账号二", now=100)
        channel = queue.Queue()
        item = dict(id="receipt", kind="incoming", account="A1", uid="uid-a",
                    payload=incoming("A2", "uid-b"), durable=True)
        with pytest.raises(ValueError):
            handle_worker_event(store, item, {"A1": channel})
        assert channel.empty()
        assert store.db.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 0
    finally:
        store.close()


def test_test_mode_filters_other_customers(tmp_path):
    store = Store(tmp_path / "bridge.sqlite")
    try:
        store.bind_account("A1", "uid-a", "账号一", now=100)
        channel = queue.Queue()
        item = dict(id="receipt", kind="incoming", account="A1", uid="uid-a", payload=incoming(), durable=True)
        handle_worker_event(store, item, {"A1": channel}, test_customer="different-test-buyer")
        assert store.db.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 0
        assert not channel.empty()
    finally:
        store.close()
