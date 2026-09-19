"""协议探针的注册、全帧接收、有界查询和未知发送保护。"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from goofish_bridge import goofish_adapter as adapter
from goofish_bridge.probe_store import ProbeStore


class FakeSocket:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.frames:
            return json.dumps(self.frames.pop(0), ensure_ascii=False)
        await asyncio.Event().wait()

    async def send(self, raw):
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_vulcan_without_registration_ack_is_not_ready(tmp_path):
    store = ProbeStore(tmp_path)
    client = adapter.ProbeConnection(None, store, "test")
    client.ws = FakeSocket([{"lwp": "/s/vulcan"}])
    client.mids = {"reg": "reg"}
    client.reader = asyncio.create_task(client.receive())
    try:
        with pytest.raises(TimeoutError):
            await client.wait_ready(0.03)
    finally:
        client.reader.cancel()
        await asyncio.gather(client.reader, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_all_pushes_persist_before_ack(tmp_path):
    store = ProbeStore(tmp_path)
    received = []
    frame = {"lwp": "/s/vulcan", "body": {"syncPushPackage": {"data": [
        {"data": json.dumps({"1": "cid@goofish", "2": 1, "3": f"msg-{i}"})}
        for i in range(3)
    ]}}}
    client = adapter.ProbeConnection(None, store, "test", received.append)
    client.ws = FakeSocket([{"code": 200, "headers": {"mid": "reg"}}, frame])
    client.mids = {"reg": "reg"}
    client.reader = asyncio.create_task(client.receive())
    try:
        await client.wait_ready(0.3)
        assert len(received) == 3
        assert store.connection.execute("SELECT count(*) FROM probe_events").fetchone()[0] == 2
        assert len(client.ws.sent) == 1
    finally:
        client.reader.cancel()
        await asyncio.gather(client.reader, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_disk_failure_does_not_ack():
    def fail(*args):
        raise OSError("模拟磁盘写入失败")

    client = adapter.ProbeConnection(None, SimpleNamespace(event=fail), "test")
    client.ws = FakeSocket([{"lwp": "/s/vulcan"}])
    client.mids = {"reg": "reg"}
    with pytest.raises(OSError):
        await client.receive()
    assert not client.ws.sent


@pytest.mark.asyncio
async def test_send_timeout_is_unknown_without_retry(tmp_path, monkeypatch):
    calls = []

    async def request(frame):
        calls.append(frame)
        assert store.connection.execute("SELECT state FROM probe_sends").fetchone()[0] == "DISPATCHING"
        raise TimeoutError

    @asynccontextmanager
    async def fake_connection(*args):
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(adapter, "connection", fake_connection)
    monkeypatch.setattr(adapter.guard, "check", lambda: None)
    monkeypatch.setattr(adapter.limiter, "check", lambda _: None)
    store = ProbeStore(tmp_path)
    try:
        result = await adapter.send_probe(SimpleNamespace(unb="account"), store, "A1", "cid", "buyer", " 原文 ")
        assert result["state"] == "UNKNOWN"
        assert len(calls) == 1
        assert store.connection.execute("SELECT state FROM probe_sends").fetchone()[0] == "UNKNOWN"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_history_repeated_cursor_reports_gap(monkeypatch):
    calls = []

    async def request(frame):
        calls.append(frame)
        assert frame["headers"]["mid"].endswith(" 0"), "请求标识必须保留已验证的上游协议格式"
        return {"code": 200, "body": {"userMessageModels": [], "hasMore": 1, "nextCursor": 9007199254740991}}

    @asynccontextmanager
    async def fake_connection(*args):
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(adapter, "connection", fake_connection)
    result = await adapter.probe_history(None, None, "cid")
    assert not result["complete"]
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["", "wrong@other", "@goofish", " x", None, 123])
def test_untrusted_identifiers_rejected(value):
    with pytest.raises(ValueError):
        adapter.normalize_id(value)


def test_route_matches_marker_and_excludes_self():
    frame = {"1": {"1": {"1": "source-candidate"}, "2": "chat@goofish",
                   "5": "timestamp-candidate", "10": {
                       "senderUserId": "buyer", "reminderContent": "MVP-TEST"}}}
    route = adapter.match_test_route(frame, "MVP-TEST", "account")
    assert route["cid"] == "chat"
    assert route["customer_uid"] == "buyer"
    assert route["candidate_fields_unverified"]["1.1.1"] == "source-candidate"
    assert adapter.match_test_route(frame, "other-marker", "account") is None
    assert adapter.match_test_route(frame, "MVP-TEST", "buyer") is None


def test_history_comparison_requires_complete_identifier():
    value = {"messages": [{"messageId": "123.PNM", "createdAt": 1789794132942}]}
    assert adapter.matching_paths(value, "123.PNM") == ["body.messages[0].messageId"]
    assert adapter.matching_paths(value, "123") == []
    assert adapter.matching_paths(value, "1789794132942") == ["body.messages[0].createdAt"]


@pytest.mark.asyncio
async def test_roundtrip_reuses_the_listening_connection(monkeypatch):
    client = SimpleNamespace()
    incoming = {"event": "message", "cid": "cid", "send_user_id": "buyer"}

    @asynccontextmanager
    async def fake_connection(*args):
        callback = args[3]
        client.reader = asyncio.create_task(asyncio.Event().wait())
        callback(incoming)

        async def observe(seconds):
            callback(incoming)

        client.observe = observe
        try:
            yield client
        finally:
            client.reader.cancel()
            await asyncio.gather(client.reader, return_exceptions=True)

    async def send(*args, client=None):
        assert client is not None, "发送必须复用监听连接，禁止另开连接顶掉监听"
        return {"state": "SERVER_ACCEPTED"}

    monkeypatch.setattr(adapter, "connection", fake_connection)
    monkeypatch.setattr(adapter, "send_probe", send)
    result = await adapter.probe_roundtrip(None, None, "A1", "cid", "buyer", "测试", 0.1)
    assert result["received_after_send"]


@pytest.mark.asyncio
async def test_verified_empty_history_response_is_complete(monkeypatch):
    async def request(frame):
        return {"code": 200, "body": {"nextCursor": 0, "hasMore": 0, "degradeFailover": 0}}

    @asynccontextmanager
    async def fake_connection(*args):
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(adapter, "connection", fake_connection)
    result = await adapter.probe_history(None, None, "chat")
    assert result["complete"]
    assert result["messages"] == 0


@pytest.mark.asyncio
async def test_expired_registration_requires_manual_authentication(tmp_path):
    from goofish_cli.core.errors import AuthRequiredError

    store = ProbeStore(tmp_path)
    client = adapter.ProbeConnection(None, store, "test")
    client.ws = FakeSocket([{"code": 401, "headers": {"mid": "reg"}}])
    client.mids = {"reg": "reg"}
    try:
        with pytest.raises(AuthRequiredError):
            await client.receive()
    finally:
        store.close()
