"""账号进程的发送阶段及账号身份检查，使用模拟连接。"""

import asyncio
import base64
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from goofish_bridge import account_worker


@pytest.mark.asyncio
async def test_image_summary_triggers_history_fetch(monkeypatch):
    worker = account_worker.Worker.__new__(account_worker.Worker)
    worker.uid = "seller"
    worker.pending_history = {}
    seen = []

    async def ingest(event):
        seen.append(event)

    worker.ingest = ingest
    image = {"cid": "chat", "customer_uid": "buyer", "source_message_id": "image-id",
             "text": "[图片]", "message_type": "text_or_summary"}
    monkeypatch.setattr(account_worker, "from_push", lambda *args: image)
    worker.key, worker.timezone = "A1", "UTC"
    await worker.decoded({"1": {}})
    assert seen == [image]
    assert worker.pending_history == {"chat": "image-id"}


def test_worker_applies_configured_rate_limit(monkeypatch, tmp_path):
    config = SimpleNamespace(raw={"bridge": {"write_rpm_per_account": 12}, "app": {}})
    initialized = []

    def initialize(config, key):
        # 保留真实初始化的关键副作用，包括 Worker 构造时的第二次覆盖。
        initialized.append(key)
        monkeypatch.setenv("GOOFISH_WRITE_RPM", "1")

    monkeypatch.setenv("GOOFISH_WRITE_RPM", "1")
    monkeypatch.setattr(account_worker.Config, "load", lambda _: config)
    monkeypatch.setattr(account_worker, "initialize_account", initialize)
    monkeypatch.setattr(account_worker, "load_session", lambda *_: SimpleNamespace(unb="account"))
    monkeypatch.setattr(account_worker, "account_lock", lambda _: nullcontext())
    monkeypatch.setattr(account_worker.limiter, "STATE_PATH", tmp_path / "limiter.json")
    ticks = iter([100 + 5 * i for i in range(13)])
    monkeypatch.setattr(account_worker.limiter.time, "time", lambda: next(ticks))

    async def run(self):
        assert initialized == ["A1", "A1"]
        assert account_worker.limiter._rpm() == 12
        for _ in range(13):
            account_worker.limiter.check("message.write")

    monkeypatch.setattr(account_worker.Worker, "run", run)
    account_worker.worker_main("config.yaml", "A1", None, None, None,
                               {"positions": [], "monitor_start": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("accept", "SERVER_ACCEPTED"), ("timeout", "UNKNOWN"),
                                            ("wrong-account", "FAILED"), ("expired", "EXPIRED")])
async def test_worker_send_stages(monkeypatch, mode, expected):
    sent, reports = [], []

    async def request(frame):
        sent.append(frame)
        payload = json.loads(base64.b64decode(frame["body"][0]["content"]["custom"]["data"]))
        assert payload["text"]["text"] == " 原文不改 "
        assert frame["body"][1]["actualReceivers"] == ["buyer@goofish", "account@goofish"]
        if mode == "timeout":
            raise TimeoutError
        return {"code": 200, "body": {"messageId": "server-id"}}

    async def emit(kind, payload, durable=False):
        reports.append((kind, payload, durable))

    worker = account_worker.Worker.__new__(account_worker.Worker)
    worker.key, worker.uid = "A1", "account"
    worker.config, worker.paths = None, None
    worker.session = SimpleNamespace(unb="account")
    worker.client = SimpleNamespace(reader=SimpleNamespace(done=lambda: False), request=request)
    worker.emit = emit
    monkeypatch.setattr(account_worker, "validate_uid", lambda *args: None)
    monkeypatch.setattr(account_worker.guard, "check", lambda: None)
    monkeypatch.setattr(account_worker.limiter, "check", lambda _: None)
    task = dict(task_id="task", account_key="A1", account_uid="other" if mode == "wrong-account" else "account",
                expires_at=time.time() + (-10 if mode == "expired" else 60), cid="chat", customer_uid="buyer",
                text=" 原文不改 ", request_id="request", client_uuid="uuid")
    await worker.send(task)
    assert reports[-1][1]["state"] == expected
    assert reports[-1][2] is True
    assert len(sent) == (1 if mode in {"accept", "timeout"} else 0)


@pytest.mark.asyncio
async def test_ipc_receipt_waits_for_main_process_commit():
    class EventQueue:
        def put(self, event, block, timeout):
            emitted.append(event)

    emitted = []
    worker = account_worker.Worker.__new__(account_worker.Worker)
    worker.events = EventQueue()
    worker.futures = {}
    worker.key, worker.uid = "A1", "account"
    task = asyncio.create_task(worker.emit("incoming", {"text": "测试"}, durable=True))
    for _ in range(100):
        if emitted:
            break
        await asyncio.sleep(0.01)
    assert emitted
    assert not task.done()
    worker.futures[emitted[0]["id"]].set_result(True)
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("accept", "SERVER_ACCEPTED"), ("upload-fail", "FAILED"),
    ("timeout", "UNKNOWN"), ("expires-during-upload", "EXPIRED"),
    ("disconnect-during-upload", "FAILED"), ("wrong-account", "FAILED"),
])
async def test_worker_image_send_stages(monkeypatch, mode, expected):
    sent, reports, uploads = [], [], []
    disconnected = False

    async def request(frame):
        sent.append(frame)
        custom = frame["body"][0]["content"]["custom"]
        assert custom["type"] == 2
        payload = json.loads(base64.b64decode(custom["data"]))
        assert payload == {"contentType": 2, "image": {"pics": [
            {"type": 0, "url": "https://img.alicdn.com/test.jpg", "width": 20, "height": 30}]}}
        assert frame["body"][1]["actualReceivers"] == ["buyer@goofish", "account@goofish"]
        if mode == "timeout":
            raise TimeoutError
        return {"code": 200, "body": {"messageId": "server-image"}}

    async def emit(kind, payload, durable=False):
        reports.append(payload)

    def prepare(config, session, task):
        nonlocal disconnected
        assert session is worker.session
        uploads.append(task)
        if mode == "upload-fail":
            raise account_worker.MediaError("闲鱼图片上传失败")
        if mode == "expires-during-upload":
            task["expires_at"] = 0
        if mode == "disconnect-during-upload":
            disconnected = True
        return {"url": "https://img.alicdn.com/test.jpg", "width": 20, "height": 30}

    worker = account_worker.Worker.__new__(account_worker.Worker)
    worker.key, worker.uid = "A1", "account"
    worker.config, worker.paths = None, None
    worker.session = SimpleNamespace(unb="account")
    worker.client = SimpleNamespace(reader=SimpleNamespace(done=lambda: disconnected), request=request)
    worker.emit = emit
    monkeypatch.setattr(account_worker, "prepare_image", prepare)
    monkeypatch.setattr(account_worker, "validate_uid", lambda *args: None)
    monkeypatch.setattr(account_worker.guard, "check", lambda: None)
    monkeypatch.setattr(account_worker.limiter, "check", lambda _: None)
    task = dict(task_id="image-task", account_key="A1", account_uid="other" if mode == "wrong-account" else "account",
                expires_at=time.time() + 60, cid="chat", customer_uid="buyer", text="[图片]",
                request_id="request", client_uuid="uuid", message_type="image", image_key="img-test")
    await worker.send(task)
    assert reports[-1]["state"] == expected
    assert len(sent) == (1 if mode in {"accept", "timeout"} else 0)
    assert len(uploads) == (0 if mode == "wrong-account" else 1)
    if mode == "upload-fail":
        assert reports[-1]["error"] == "闲鱼图片上传失败"
