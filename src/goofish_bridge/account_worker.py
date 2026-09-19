"""每账号独立进程：唯一接收循环、发送队列和有界恢复，不直接写业务库。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import random
import time
from pathlib import Path
from uuid import uuid4

from goofish_bridge.account import account_lock, initialize_account, load_session, validate_uid
from goofish_bridge.config import Config
from goofish_bridge.goofish_adapter import connection, history_models, normalize_id
from goofish_bridge.media import MediaError, prepare_image
from goofish_bridge.messages import from_history, from_push
from goofish_cli.core import guard, limiter
from goofish_cli.core.errors import AuthRequiredError, RiskControlError
from goofish_cli.core.mtop import call
from goofish_cli.core.sign import generate_mid
from goofish_cli.core.ws import extract_meta_event


class Worker:
    def __init__(self, config, key, commands, events, stop, initial):
        self.config, self.key = config, key
        self.commands, self.events, self.stop = commands, events, stop
        self.paths = initialize_account(config, key)
        self.session = load_session(config, self.paths)
        self.uid = self.session.unb
        self.futures = {}
        self.client = None
        self.sending = None
        self.pending_history = {}
        self.positions = {r["cid"]: r["watermark"] for r in initial["positions"]}
        self.monitor_start = initial["monitor_start"]
        self.checkpoints = {r["cid"]: r.get("checkpoint") or self.monitor_start for r in initial["positions"]}
        self.timezone = config.raw["app"].get("display_timezone", "Asia/Shanghai")
        self.seen_sources = set()
        self.last_history_request = 0.0
        self.manual_pause = False

    async def emit(self, kind, payload, durable=False):
        identifier = uuid4().hex
        future = asyncio.get_running_loop().create_future() if durable else None
        if future:
            self.futures[identifier] = future
        try:
            await asyncio.to_thread(self.events.put, {
                "id": identifier, "kind": kind, "account": self.key, "uid": self.uid,
                "payload": payload, "durable": durable,
            }, True, 5)
            if future:
                await asyncio.wait_for(future, 15)
        finally:
            self.futures.pop(identifier, None)

    async def ingest(self, event):
        if event is None:
            return
        await self.emit("incoming", event, durable=True)
        if event.get("source_message_id"):
            self.seen_sources.add(event["source_message_id"])
            if len(self.seen_sources) > 4000:
                self.seen_sources.clear()
        if event.get("cid") and event.get("source_time"):
            self.positions[event["cid"]] = max(self.positions.get(event["cid"], self.monitor_start), event["source_time"])

    async def decoded(self, raw):
        event = from_push(raw, self.key, self.uid, self.timezone)
        if event:
            await self.ingest(event)
            return
        meta = extract_meta_event(raw)
        if meta and meta["event"] == "new_msg":
            if meta.get("msg_id") not in self.seen_sources:
                self.pending_history[meta["cid"]] = meta.get("msg_id", "")
            return
        operation = raw.get("operation")
        if isinstance(operation, dict) and raw.get("sessionId"):
            # 会话激活只能提供 CID，绝不把商品卖家 ID 当作客户。
            self.pending_history[normalize_id(raw["sessionId"])] = ""
        elif isinstance(raw.get("1"), dict):
            await self.emit("notice", "收到暂不支持解析的闲鱼事件，请到闲鱼核对。", durable=True)
        if len(self.pending_history) > 100:
            self.pending_history.clear()
            await self.emit("notice", "补拉会话超过本轮 100 个上限，补拉未完整，请到闲鱼核对。", durable=True)

    async def read_history(self, cid, expected="", recovering=False):
        positions = self.checkpoints if recovering else self.positions
        since = positions.get(cid, self.monitor_start) - 2
        cursor, seen, complete = 9007199254740991, set(), False
        matched = not expected or expected in self.seen_sources
        reason = "历史达到页数上限"
        try:
            async with asyncio.timeout(30):
                for _page in range(5):
                    await asyncio.sleep(max(0, 1 - (time.monotonic() - self.last_history_request)))
                    self.last_history_request = time.monotonic()
                    response = await self.client.request({"lwp": "/r/MessageManager/listUserMessages",
                        "headers": {"mid": generate_mid()},
                        "body": [f"{cid}@goofish", False, cursor, 20, False]})
                    if response.get("code") != 200:
                        reason = f"历史查询被拒绝（代码 {response.get('code')}）"
                        break
                    body = response.get("body") or {}
                    models = history_models(body)
                    if not isinstance(models, list):
                        reason = "历史正文结构未知"
                        break
                    oldest = time.time()
                    for model in reversed(models):
                        event = from_history(model, self.key, self.uid, self.timezone)
                        if event["source_message_id"] == expected:
                            matched = True
                        if event["source_time"] is not None:
                            oldest = min(oldest, event["source_time"])
                        if event["source_time"] is None or event["source_time"] >= since:
                            await self.ingest(event)
                    if body.get("hasMore") == 0 or (models and oldest < since):
                        complete, reason = True, ""
                        break
                    cursor = body.get("nextCursor")
                    if not isinstance(cursor, (str, int)) or cursor in seen:
                        reason = "历史游标异常"
                        break
                    seen.add(cursor)
                    await asyncio.sleep(1)
        except TimeoutError:
            reason = "历史补拉超时"
        if not matched:
            complete, reason = False, "轻量通知对应正文未补齐"
        await self.emit("sync", {"cid": cid, "complete": complete, "gap": reason}, durable=True)
        if complete:
            self.checkpoints[cid] = self.positions.get(cid, self.monitor_start)

    async def catchup(self):
        # 先发现离线期间的新会话。上游接口只提供有限基线，不能谎称全量覆盖。
        raw = await asyncio.to_thread(call, self.session,
            api="mtop.taobao.idlemessage.pc.session.sync", data={"fetchNum": 50}, version="3.0")
        data = raw.get("data") or {}
        cids = set(self.positions)
        for item in data.get("sessions") or []:
            cid = (item.get("session") or {}).get("sessionId")
            if cid:
                cids.add(normalize_id(str(cid)))
        if data.get("hasMore") or len(cids) > 50:
            await self.emit("notice", "会话发现达到有限基线上限，补拉未完整，请到闲鱼核对。", durable=True)
        async with asyncio.timeout(60):
            for cid in sorted(cids)[:50]:
                await self.read_history(cid, recovering=True)
                await asyncio.sleep(1)

    async def send(self, task):
        state, server_id, error = "FAILED", "", ""
        attempted = False
        try:
            validate_uid(self.config, self.paths, self.session.unb)
            if task["account_uid"] != self.uid or task["account_key"] != self.key:
                raise ValueError("发送目标账号身份冲突")
            if task["expires_at"] <= time.time():
                state = "EXPIRED"
                return
            if self.client is None or self.client.reader.done():
                raise ConnectionError("发送前监听连接不可用")
            cid, customer = normalize_id(task["cid"]), normalize_id(task["customer_uid"])
            kind = task.get("message_type", "text")
            if customer == self.uid or kind not in {"text", "image"}:
                raise ValueError("发送目标或正文不合法")
            if kind == "text" and (not task["text"].strip() or len(task["text"]) > 2000):
                raise ValueError("发送目标或正文不合法")
            if kind == "image" and not task.get("image_key"):
                raise MediaError("图片资源标识缺失，请重新引用发送图片")
            guard.check()
            limiter.check("message.write")
            payload = {"contentType": 1, "text": {"text": task["text"]}}
            content_type = 1
            if kind == "image":
                uploaded = await asyncio.to_thread(prepare_image, self.config, self.session, task)
                if task["expires_at"] <= time.time():
                    state = "EXPIRED"
                    return
                if self.client is None or self.client.reader.done():
                    raise ConnectionError("图片上传后监听连接不可用，未发送")
                guard.check()
                content_type = 2
                payload = {"contentType": 2, "image": {"pics": [{"type": 0, **uploaded}]}}
            encoded = base64.b64encode(json.dumps(payload,
                                       ensure_ascii=False).encode("utf-8")).decode("ascii")
            frame = {"lwp": "/r/MessageSend/sendByReceiverScope", "headers": {"mid": task["request_id"]},
                     "body": [{"uuid": task["client_uuid"], "cid": f"{cid}@goofish", "conversationType": 1,
                       "content": {"contentType": 101, "custom": {"type": content_type, "data": encoded}},
                       "redPointPolicy": 0, "extension": {"extJson": "{}"},
                       "ctx": {"appVersion": "1.0", "platform": "web"}, "mtags": {}, "msgReadStatusSetting": 1},
                       {"actualReceivers": [f"{customer}@goofish", f"{self.uid}@goofish"]}]}
            attempted = True
            ack = await self.client.request(frame)
            code = ack.get("code")
            state = "SERVER_ACCEPTED" if code == 200 else "FAILED" if isinstance(code, int) else "UNKNOWN"
            server_id = str((ack.get("body") or {}).get("messageId") or "")
            if code in (401, 403):
                self.manual_pause = True
                await self.emit("state", {"state": "AUTH_REQUIRED" if code == 401 else "RISK_PAUSED",
                                          "error": f"发送接口代码 {code}"})
        except (Exception, asyncio.CancelledError) as exc:
            state = "UNKNOWN" if attempted else "FAILED"
            error = str(exc) if isinstance(exc, MediaError) else type(exc).__name__
            if isinstance(exc, RiskControlError):
                self.manual_pause = True
                await self.emit("state", {"state": "RISK_PAUSED", "error": error})
        finally:
            await self.emit("result", {"task_id": task["task_id"], "state": state,
                                      "message_id": server_id, "error": error}, durable=True)

    async def command_loop(self):
        while not self.stop.is_set():
            try:
                command = await asyncio.to_thread(self.commands.get, True, 0.5)
            except queue.Empty:
                continue
            if command["kind"] == "committed":
                future = self.futures.get(command["id"])
                if future and not future.done():
                    future.set_result(True)
            elif command["kind"] == "send":
                if self.sending and not self.sending.done():
                    raise RuntimeError("主进程发送并发违反每账号串行限制")
                self.sending = asyncio.create_task(self.send(command["task"]))

    async def run(self):
        pump = asyncio.create_task(self.command_loop())
        attempt = 0
        try:
            while not self.stop.is_set():
                try:
                    await self.emit("state", {"state": "STARTING" if attempt == 0 else "RECONNECTING"})
                    async with connection(self.session, None, "runtime", on_decoded=self.decoded) as client:
                        self.client = client
                        await self.emit("state", {"state": "ONLINE"})
                        attempt = 0
                        try:
                            async with asyncio.timeout(60):
                                await self.catchup()
                        except TimeoutError:
                            await self.emit("notice", "账号恢复补拉达到 60 秒上限，请到闲鱼核对缺口。", durable=True)
                        while not self.stop.is_set():
                            if self.manual_pause:
                                while not self.stop.is_set():
                                    await asyncio.sleep(0.5)
                                break
                            if time.monotonic() - client.last_received > 60:
                                raise TimeoutError("60 秒没有收到服务端活动，重新验证连接")
                            if pump.done():
                                await pump
                                raise RuntimeError("账号命令通道已停止")
                            if self.pending_history:
                                cid, expected = self.pending_history.popitem()
                                await self.read_history(cid, expected)
                            else:
                                await client.observe(0.5)
                except (AuthRequiredError, RiskControlError, ValueError) as exc:
                    status = "RISK_PAUSED" if isinstance(exc, RiskControlError) else "AUTH_REQUIRED"
                    await self.emit("state", {"state": status, "error": type(exc).__name__})
                    while not self.stop.is_set():
                        await asyncio.sleep(0.5)
                    break
                except Exception as exc:
                    attempt += 1
                    await self.emit("state", {"state": "RECONNECTING", "error": type(exc).__name__})
                    await asyncio.sleep(min(30, 2 ** min(attempt, 5)) + random.random())
                finally:
                    self.client = None
        finally:
            if self.sending and not self.sending.done():
                self.sending.cancel()
                await asyncio.gather(self.sending, return_exceptions=True)
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            self.session.http.close()


def worker_main(config_path, key, commands, events, stop, initial):
    from loguru import logger

    logger.disable("goofish_cli")
    config = Config.load(Path(config_path))
    paths = initialize_account(config, key)
    with account_lock(paths):
        worker = Worker(config, key, commands, events, stop, initial)
        # Worker 构造也会初始化账号并重置限流，必须在全部初始化后应用桥接额度。
        os.environ["GOOFISH_WRITE_RPM"] = str(config.raw["bridge"]["write_rpm_per_account"])
        asyncio.run(worker.run())
