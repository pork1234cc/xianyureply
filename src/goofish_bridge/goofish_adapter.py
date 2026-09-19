"""单账号有限时长协议探针；收包仅由一个协程负责。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from contextlib import asynccontextmanager, nullcontext, suppress

from goofish_bridge.network import NetworkProfile
from goofish_bridge.probe_store import ProbeStore
from goofish_cli.core import guard, limiter
from goofish_cli.core.errors import AuthRequiredError, RiskControlError
from goofish_cli.core.sign import generate_mid, generate_uuid
from goofish_cli.core.token import get_access_token
from goofish_cli.core.ws import (
    WS_URL,
    _handshake_headers,
    build_ack,
    extract_incoming_text,
    extract_meta_event,
    extract_push_messages,
    heartbeat_loop,
    register,
)


def normalize_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("会话和客户 ID 必须是非空字符串")
    result = value.removesuffix("@goofish")
    if not result or "@" in result or result.strip() != result:
        raise ValueError("ID 含不支持的后缀或空白")
    return result


class ProbeConnection:
    def __init__(self, session, store: ProbeStore, channel: str, on_message=None, on_decoded=None):
        self.session = session
        self.store = store
        self.channel = channel
        self.on_message = on_message
        self.on_decoded = on_decoded
        self.pending: dict[str, asyncio.Future] = {}
        self.ready = asyncio.Event()
        self.reg_ok = False
        self.vulcan_seen = False
        self.frames = 0
        self.messages = 0
        self.notifications = 0
        self.last_received = time.monotonic()

    async def receive(self):
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                self.last_received = time.monotonic()
                if not isinstance(frame, dict):
                    raise ValueError("服务端帧不是对象")
                decoded = extract_push_messages(frame)
                # 必须持久化成功才能 ACK；脱敏结构只用于阶段 0 诊断。
                if self.store is not None:
                    self.store.event(self.channel, {"frame": frame, "decoded": decoded})
                self.frames += 1
                mid = (frame.get("headers") or {}).get("mid")
                if mid == self.mids["reg"]:
                    if frame.get("code") == 401:
                        raise AuthRequiredError("IM 认证失效，需要人工重新登录")
                    if frame.get("code") == 403:
                        raise RiskControlError("IM 注册被平台拒绝，需要人工核对")
                    if frame.get("code") != 200:
                        raise RuntimeError("IM 注册被拒绝，请人工检查账号状态")
                    self.reg_ok = True
                if frame.get("lwp") == "/s/vulcan":
                    self.vulcan_seen = True
                if self.reg_ok and self.vulcan_seen:
                    self.ready.set()
                for item in decoded:
                    if not isinstance(item, dict):
                        continue
                    if self.on_decoded:
                        result = self.on_decoded(item)
                        if inspect.isawaitable(result):
                            await result
                    event = extract_meta_event(item) or extract_incoming_text(item)
                    if event:
                        self.messages += event["event"] == "message"
                        self.notifications += event["event"] == "new_msg"
                        if self.on_message:
                            self.on_message(event)
                future = self.pending.get(mid)
                if future is not None and not future.done():
                    future.set_result(frame)
                if frame.get("lwp"):
                    await self.ws.send(json.dumps(build_ack(frame), ensure_ascii=False))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("接收连接已关闭"))

    async def wait_ready(self, timeout=20):
        waiter = asyncio.create_task(self.ready.wait())
        try:
            done, _ = await asyncio.wait({waiter, self.reader}, timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if self.reader in done:
                await self.reader
                raise ConnectionError("连接在注册完成前关闭")
            if waiter not in done:
                raise TimeoutError("没有同时收到注册成功回包和会话就绪事件")
        finally:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter

    async def observe(self, seconds: float):
        done, _ = await asyncio.wait({self.reader}, timeout=seconds)
        if done:
            await self.reader
            raise ConnectionError("监听提前关闭")

    async def request(self, frame: dict, timeout=15):
        if self.reader.done():
            await self.reader
            raise ConnectionError("接收连接已关闭，不能发起新请求")
        mid = frame["headers"]["mid"]
        future = asyncio.get_running_loop().create_future()
        self.pending[mid] = future
        try:
            await self.ws.send(json.dumps(frame, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(mid, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()


@asynccontextmanager
async def connection(session, store: ProbeStore, channel: str, on_message=None, on_decoded=None):
    guard.check()
    with guard.watch():
        token = await asyncio.to_thread(get_access_token, session)
    async with NetworkProfile().websocket(
        WS_URL, additional_headers=_handshake_headers(session),
        ping_interval=None, max_size=4 * 1024 * 1024, open_timeout=15,
    ) as ws:
        client = ProbeConnection(session, store, channel, on_message, on_decoded)
        client.ws = ws
        client.mids = await register(ws, session, token)
        client.reader = asyncio.create_task(client.receive())
        heartbeat = asyncio.create_task(heartbeat_loop(ws))
        try:
            await client.wait_ready()
            yield client
        finally:
            for task in (heartbeat, client.reader):
                task.cancel()
            await asyncio.gather(heartbeat, client.reader, return_exceptions=True)


async def probe_watch(session, store, seconds=60):
    async with connection(session, store, "goofish-listen") as client:
        print("闲鱼注册已获服务端接受，监听验证已开始。请使用自有测试客户发送消息。", flush=True)
        await client.observe(seconds)
        return {"frames": client.frames, "messages": client.messages,
                "new_msg_notifications": client.notifications,
                "source_identity_verified": False}


def match_test_route(decoded, marker, account_uid):
    """精确匹配用户从测试客户发来的随机标记，不根据最近客户猜目标。"""
    event = extract_incoming_text(decoded)
    if (not event or event.get("send_message") != marker or not event.get("cid")
            or not event.get("send_user_id")):
        return None
    customer = normalize_id(event["send_user_id"])
    if customer == account_uid:
        return None
    # 数字字段仅记录为待核对候选；在历史交叉验证前不能用于正式消息身份。
    one = decoded.get("1")
    fields = {}
    if isinstance(one, dict):
        for key in ("1", "3", "5", "7"):
            value = one.get(key)
            if key == "1" and isinstance(value, dict):
                value = value.get("1")
            if isinstance(value, (str, int)):
                fields[f"1.{key}" + (".1" if key == "1" else "")] = value
    return {"account_uid": account_uid, "cid": normalize_id(event["cid"]),
            "customer_uid": customer, "candidate_fields_unverified": fields}


async def capture_test_route(session, store, marker, seconds=120):
    if not marker or len(marker) > 100:
        raise ValueError("测试标记必须为 1～100 字符")
    routes = []
    found = asyncio.Event()

    def decoded(item):
        route = match_test_route(item, marker, session.unb)
        if route:
            routes.append(route)
            found.set()

    async with connection(session, store, "goofish-test-route", on_decoded=decoded) as client:
        print(f"请从自有测试客户向当前闲鱼账号发送完整标记：{marker}", flush=True)
        waiter = asyncio.create_task(found.wait())
        try:
            done, _ = await asyncio.wait({waiter, client.reader}, timeout=seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            if client.reader in done:
                await client.reader
                raise ConnectionError("测试客户识别时监听中断")
            if waiter not in done:
                raise TimeoutError("未收到精确匹配的测试标记")
            unique = {(r["cid"], r["customer_uid"]) for r in routes}
            if len(unique) != 1:
                raise ValueError("同一标记对应多个会话，拒绝选择测试目标")
            return routes[0]
        finally:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter


def matching_paths(value, expected: str, prefix="body"):
    """在真实历史中按完整值寻找字段路径，供人工核对协议身份。"""
    if not expected:
        return []
    if isinstance(value, dict):
        return [p for key, item in value.items()
                for p in matching_paths(item, expected, f"{prefix}.{key}")]
    if isinstance(value, list):
        return [p for index, item in enumerate(value)
                for p in matching_paths(item, expected, f"{prefix}[{index}]")]
    return [prefix] if isinstance(value, (str, int)) and str(value) == expected else []


def history_models(body):
    """空会话实测省略列表；只有明确无更多且未降级时才能视为空结果。"""
    models = body.get("userMessageModels")
    if "userMessageModels" not in body and body.get("hasMore") == 0 and body.get("degradeFailover") == 0:
        return []
    return models


async def probe_history(session, store, cid, max_pages=5, page_size=20, timeout=60,
                        compare_message_id="", compare_source_time=""):
    cid = normalize_id(cid)
    if not 1 <= max_pages <= 5 or not 1 <= page_size <= 20 or not 1 <= timeout <= 60:
        raise ValueError("历史探针上限：5 页、每页 20 条、总计 60 秒")
    count = 0
    complete = False
    pages = 0
    reason = "达到页数上限"
    matches = {"message_id_paths": [], "source_time_paths": []}
    try:
        async with asyncio.timeout(timeout), connection(session, store, "goofish-history") as client:
            cursor = 9007199254740991
            seen = {cursor}
            for _page in range(max_pages):
                pages += 1
                frame = {"lwp": "/r/MessageManager/listUserMessages",
                         "headers": {"mid": generate_mid()},
                         "body": [f"{cid}@goofish", False, cursor, page_size, False]}
                ack = await client.request(frame)
                if ack.get("code") != 200:
                    reason = "服务端未接受历史查询"
                    break
                body = ack.get("body") or {}
                models = history_models(body)
                if not isinstance(models, list):
                    reason = "历史正文结构不符合预期"
                    break
                count += len(models)
                matches["message_id_paths"].extend(matching_paths(body, compare_message_id))
                matches["source_time_paths"].extend(matching_paths(body, compare_source_time))
                if body.get("hasMore") in (0, False):
                    complete, reason = True, "服务端报告当前会话无更多历史"
                    break
                next_cursor = body.get("nextCursor")
                if (body.get("hasMore") != 1 or not isinstance(next_cursor, (str, int))
                        or next_cursor in seen):
                    reason = "历史游标缺失、重复或分页状态未知"
                    break
                cursor = next_cursor
                seen.add(cursor)
                await asyncio.sleep(1)
    except TimeoutError:
        reason = "达到总时间上限"
    return {"pages": pages, "messages": count, "complete": complete, "reason": reason, **matches}


async def send_probe(session, store, key, cid, customer_uid, text, *, client=None):
    cid, customer_uid = normalize_id(cid), normalize_id(customer_uid)
    if not text.strip() or len(text) > 2000:
        raise ValueError("测试正文必须为 1～2000 字符，保留原文，不自动截断")
    if customer_uid == session.unb:
        raise ValueError("测试客户不能是当前账号自身")
    if client is not None and client.session is not session:
        raise ValueError("发送连接与当前账号会话不一致")
    # 每个测试发送连接仍遵守每账号 1 次/分钟。
    guard.check()
    limiter.check("message.write")
    context = nullcontext(client) if client is not None else connection(session, store, "goofish-send")
    async with context as client:
        mid, client_uuid = generate_mid(), generate_uuid()
        payload = base64.b64encode(json.dumps(
            {"contentType": 1, "text": {"text": text}}, ensure_ascii=False,
        ).encode("utf-8")).decode("ascii")
        frame = {"lwp": "/r/MessageSend/sendByReceiverScope", "headers": {"mid": mid},
                 "body": [{"uuid": client_uuid, "cid": f"{cid}@goofish", "conversationType": 1,
                           "content": {"contentType": 101, "custom": {"type": 1, "data": payload}},
                           "redPointPolicy": 0, "extension": {"extJson": "{}"},
                           "ctx": {"appVersion": "1.0", "platform": "web"}, "mtags": {},
                           "msgReadStatusSetting": 1},
                          {"actualReceivers": [f"{customer_uid}@goofish", f"{session.unb}@goofish"]}]}
        task = store.prepare_send(key, session.unb, cid, customer_uid, mid, client_uuid)
        try:
            ack = await client.request(frame)
        except (Exception, asyncio.CancelledError) as exc:
            store.finish_send(task, "UNKNOWN", error_type=type(exc).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {"task": task, "state": "UNKNOWN", "retry": False}
        code = ack.get("code")
        state = "SERVER_ACCEPTED" if code == 200 else "FAILED" if isinstance(code, int) else "UNKNOWN"
        message_id = str((ack.get("body") or {}).get("messageId") or "")
        store.finish_send(task, state, message_id)
        return {"task": task, "state": state, "retry": False}


async def probe_roundtrip(session, store, key, cid, customer_uid, text, seconds=60):
    """先看到指定已有会话的客户消息才发送；发送后继续监听验证连接共存。"""
    cid, customer_uid = normalize_id(cid), normalize_id(customer_uid)
    seen = asyncio.Event()
    count = 0

    def on_message(event):
        nonlocal count
        if (event.get("event") == "message" and event.get("cid")
                and event.get("send_user_id")
                and normalize_id(event["cid"]) == cid
                and normalize_id(event["send_user_id"]) == customer_uid):
            count += 1
            seen.set()

    async with connection(session, store, "goofish-roundtrip-listen", on_message) as listener:
        print("已开始监听；请让指定自有测试客户在原会话发送一条消息。", flush=True)
        waiter = asyncio.create_task(seen.wait())
        try:
            done, _ = await asyncio.wait({waiter, listener.reader}, timeout=seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            if listener.reader in done:
                await listener.reader
                raise ConnectionError("测试监听连接已关闭")
            if waiter not in done:
                raise TimeoutError("未观察到指定客户和会话的消息，没有执行测试发送")
        finally:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter
        result = await send_probe(session, store, key, cid, customer_uid, text, client=listener)
        before = count
        print(f"发送状态：{result['state']}。请让测试客户再发一条消息，以验证接收仍正常。", flush=True)
        await listener.observe(seconds)
        result["received_after_send"] = count > before
        return result
