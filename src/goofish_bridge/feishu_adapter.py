"""飞书本人私聊绑定与手机直接引用验证，使用锁定版本官方 SDK。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

from goofish_bridge.network import NetworkProfile
from goofish_bridge.probe_store import ProbeStore


def build_customer_card(payload):
    """构造单客户对话卡片；卡片只展示昵称、正文和发送状态。"""
    lines = []
    entries = payload.get("entries", [])
    visible_entries = entries[-12:] if payload.get("expanded") else entries[-4:]
    for entry in visible_entries:
        speaker = entry.get("speaker", "消息")
        text = entry.get("text", "")
        status = f"（{entry['status']}）" if entry.get("status") else ""
        lines.append(f"**{speaker}**：{text}{status}")
    content = f"**{payload.get('account_name', '闲鱼账号')}** · {payload.get('customer_name', '客户')}\n\n"
    content += "\n\n".join(lines) or "暂无消息"
    view_button = {"tag": "button", "text": {"tag": "plain_text", "content": "收起" if payload.get("expanded") else "查看更多"},
                   "type": "default", "value": {"op": "collapse" if payload.get("expanded") else "expand"}}
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "闲鱼客户对话"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                {"tag": "action", "actions": [view_button]},
                {"tag": "form", "name": "reply_form", "elements": [
                    {"tag": "input", "name": "reply_text",
                     "placeholder": {"tag": "plain_text", "content": "输入回复内容"}, "max_length": 1000},
                    {"tag": "button", "name": "submit",
                     "text": {"tag": "plain_text", "content": "发送回复"}, "type": "primary",
                     "action_type": "form_submit"}
                ]}
            ]}


def operator_identity(raw: dict, app_id: str, allowed_open_id: str, binding=None):
    """先校验身份，使授权用户的非文本也能得到明确拒绝提示。"""
    header = raw.get("header") or {}
    event = raw.get("event") or {}
    sender = event.get("sender") or {}
    message = event.get("message") or {}
    open_id = (sender.get("sender_id") or {}).get("open_id")
    tenant = header.get("tenant_key")
    if (not app_id or not allowed_open_id or header.get("app_id") != app_id
            or header.get("event_type") != "im.message.receive_v1"
            or sender.get("sender_type") != "user" or open_id != allowed_open_id
            or not tenant or sender.get("tenant_key") != tenant
            or message.get("chat_type") != "p2p"
            or not message.get("message_id") or not message.get("chat_id")):
        return None
    if binding and (binding.get("app_id") != app_id or binding.get("open_id") != open_id
                    or binding.get("chat_id") != message["chat_id"]
                    or binding.get("tenant_key") != tenant):
        return None
    return {"app_id": app_id, "tenant_key": tenant, "open_id": open_id,
            "chat_id": message["chat_id"], "message_id": message["message_id"]}


def parse_operator_event(raw: dict, app_id: str, allowed_open_id: str, binding=None):
    """仅接受指定应用、本人、私聊、文本，不使用 root_id 替代 parent_id。"""
    identity = operator_identity(raw, app_id, allowed_open_id, binding)
    if identity is None:
        return None
    message = raw["event"]["message"]
    if message.get("message_type") != "text":
        return None
    try:
        content = json.loads(message.get("content") or "{}")
        text = content.get("text") if isinstance(content, dict) else None
        created = int(message.get("create_time"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(text, str) or not text.strip() or created <= 0:
        return None
    return {**identity,
            "parent_id": message.get("parent_id") or "", "create_time": created,
            "text": text}


def credentials(config):
    settings = config.raw["feishu"]
    NetworkProfile(**settings["network"])
    values = [os.getenv(settings[field], "") for field in
              ("app_id_env", "app_secret_env", "allowed_open_id_env")]
    if not all(values):
        raise ValueError("缺少飞书 App ID、Secret 或本人 OPEN_ID 环境变量")
    return values


def load_binding(config):
    path = config.root / "data" / "feishu-binding.json"
    if not path.exists():
        raise ValueError("飞书尚未确认绑定，请先执行 bind-feishu")
    return json.loads(path.read_text(encoding="utf-8"))


def configure_sdk_direct():
    import lark_oapi.core.http.transport as transport
    import lark_oapi.ws.client as ws_module
    from lark_oapi.core.log import logger

    http = NetworkProfile().http()
    endpoint_http = NetworkProfile().http()
    # 只替换 SDK 模块局部依赖，不改 requests 全局 API；版本由 uv.lock 固定。
    ws_module.requests = SimpleNamespace(post=partial(endpoint_http.post, timeout=15))
    transport.requests = SimpleNamespace(request=http.request)
    # SDK 可能将包含访问凭据的连接 URL 写到日志，诊断工具统一禁用 SDK 日志。
    logger.disabled = True
    def close():
        endpoint_http.close()
        http.close()

    return SimpleNamespace(close=close)


async def probe_events(config, store: ProbeStore, seconds=120, binding=None):
    import lark_oapi as lark
    import lark_oapi.ws.client as ws_module

    app_id, secret, open_id = credentials(config)
    result = []
    seen = set()
    found = asyncio.Event()

    def handle(event):
        parsed = parse_operator_event(json.loads(lark.JSON.marshal(event)), app_id, open_id, binding)
        if not parsed or parsed["message_id"] in seen:
            return
        store.event("feishu", parsed)
        seen.add(parsed["message_id"])
        result.append({k: v for k, v in parsed.items() if k != "text"})
        print(f"收到本人私聊文本事件；直接引用 parent_id：{'有' if parsed['parent_id'] else '无'}。", flush=True)
        found.set()

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(handle).build()
    ws_module.loop = asyncio.get_running_loop()
    http = configure_sdk_direct()
    client = lark.ws.Client(app_id, secret, event_handler=handler,
                            domain=lark.FEISHU_DOMAIN, auto_reconnect=False)
    baseline = asyncio.all_tasks()
    owned = set()
    try:
        async with asyncio.timeout(30):
            await client._connect()
        owned.update(asyncio.all_tasks() - baseline)
        print("飞书长连接已建立，请向自己的应用机器人发送一条测试文字。", flush=True)
        owned.add(asyncio.create_task(client._ping_loop()))
        waiter = asyncio.create_task(found.wait())
        done, _ = await asyncio.wait({waiter, *owned}, timeout=seconds,
                                     return_when=asyncio.FIRST_COMPLETED)
        owned.add(waiter)
        if waiter not in done:
            for task in done:
                await task
            if done:
                raise ConnectionError("飞书连接在收到测试事件前中断")
            raise TimeoutError("等待本人测试消息超时")
        return result
    finally:
        # 先取消接收循环再关闭 WebSocket，避免正常断开产生无人回收的异常。
        owned.update(asyncio.all_tasks() - baseline)
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await client._disconnect()
        http.close()


def bind_feishu(config, seconds=120):
    store = ProbeStore(config.root)
    try:
        events = asyncio.run(probe_events(config, store, seconds))
    finally:
        store.close()
    candidate = {k: events[0][k] for k in ("app_id", "tenant_key", "open_id", "chat_id")}
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    answer = input("核对为本人私聊后，输入完整 chat_id 确认绑定：").strip()
    if answer != candidate["chat_id"]:
        raise ValueError("本人私聊绑定已取消")
    path = config.root / "data" / "feishu-binding.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != candidate:
            raise ValueError("已存在不同飞书绑定，拒绝覆盖")
    else:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
    print("飞书本人私聊绑定已保存。")


def send_test_message(config):
    """仅由明确调用的命令发送一条固定测试消息，不向闲鱼发送。"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    app_id, secret, open_id = credentials(config)
    binding = load_binding(config)
    if binding["app_id"] != app_id or binding["open_id"] != open_id:
        raise ValueError("当前配置与已确认的飞书绑定不一致")
    http = configure_sdk_direct()
    delivery_id = uuid4().hex
    # 不自动重试；投递 UUID 和结果保存在本地便于核对。
    record_path = config.root / "data" / f"feishu-test-{delivery_id}.json"
    record = {"uuid": delivery_id, "state": "DISPATCHING"}
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    try:
        client = lark.Client.builder().app_id(app_id).app_secret(secret).domain(lark.FEISHU_DOMAIN).timeout(15).build()
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder().receive_id(binding["chat_id"]).msg_type("text")
            .content(json.dumps({"text": "阶段 0 引用验证：请用手机飞书直接引用本条消息，回复“引用测试”。此操作不会转发到闲鱼。"}, ensure_ascii=False))
            .uuid(delivery_id).build()).build()
        response = client.im.v1.message.create(request)
        record["state"] = "SERVER_ACCEPTED" if response.success() else "FAILED"
        record["message_id"] = response.data.message_id if response.success() else ""
        return record
    except Exception:
        record["state"] = "UNKNOWN"
        raise
    finally:
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        http.close()


class FeishuRuntime:
    """SDK 网络任务与主进程数据库隔离；事件回调仅等待快速本地落库。"""

    def __init__(self, config, events):
        import lark_oapi as lark

        self.config, self.events = config, events
        self.binding = load_binding(config)
        self.app_id, self.secret, self.open_id = credentials(config)
        if self.binding["app_id"] != self.app_id or self.binding["open_id"] != self.open_id:
            raise ValueError("飞书配置与确认绑定不符")
        self.http = configure_sdk_direct()
        self.stop = threading.Event()
        self.online = threading.Event()
        self.thread = None
        self.api = lark.Client.builder().app_id(self.app_id).app_secret(self.secret).domain(lark.FEISHU_DOMAIN).timeout(15).build()

    def send(self, delivery):
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            PatchMessageRequest,
            PatchMessageRequestBody,
        )

        try:
            if delivery["kind"] in {"CUSTOMER_CARD", "CUSTOMER_CARD_UPDATE"}:
                card = build_customer_card(json.loads(delivery["text"]))
                content = json.dumps(card, ensure_ascii=False)
                if delivery.get("target_message_id"):
                    request = PatchMessageRequest.builder().message_id(delivery["target_message_id"]).request_body(
                        PatchMessageRequestBody.builder().content(content).build()).build()
                    response = self.api.im.v1.message.patch(request)
                    if response.success():
                        return "SERVER_ACCEPTED", delivery["target_message_id"], ""
                    # 旧卡片可能是另一种 schema，无法原地更新时改为创建新卡片。
                    # 新消息 ID 会由 finish_outbox 写回卡片映射，后续继续原地更新。
                    if getattr(response, "code", None) not in {230099, 200830}:
                        detail = getattr(response, "msg", "") or ""
                        return "FAILED", None, f"接口代码 {response.code}{(': ' + detail) if detail else ''}"
                    request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
                        CreateMessageRequestBody.builder().receive_id(self.binding["chat_id"])
                        .msg_type("interactive").content(content).uuid(delivery["delivery_id"]).build()).build()
                    response = self.api.im.v1.message.create(request)
                else:
                    request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
                        CreateMessageRequestBody.builder().receive_id(self.binding["chat_id"])
                        .msg_type("interactive").content(content).uuid(delivery["delivery_id"]).build()).build()
                    response = self.api.im.v1.message.create(request)
            else:
                request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
                    CreateMessageRequestBody.builder().receive_id(self.binding["chat_id"]).msg_type("text")
                    .content(json.dumps({"text": delivery["text"]}, ensure_ascii=False))
                    .uuid(delivery["delivery_id"]).build()).build()
                response = self.api.im.v1.message.create(request)
            if response.success() and response.data and response.data.message_id:
                return "SERVER_ACCEPTED", response.data.message_id, ""
            if response.success():
                return "UNKNOWN", None, "成功回包缺少消息 ID，需要人工核对"
            detail = getattr(response, "msg", "") or ""
            return "FAILED", None, f"接口代码 {response.code}{(': ' + detail) if detail else ''}"
        except Exception as exc:
            return "UNKNOWN", None, type(exc).__name__

    def start(self):
        self.thread = threading.Thread(target=lambda: asyncio.run(self.listen()), name="feishu-events", daemon=True)
        self.thread.start()

    async def listen(self):
        import lark_oapi as lark
        import lark_oapi.ws.client as ws_module
        from lark_oapi.ws.exception import ClientException

        def handle(data):
            raw = json.loads(lark.JSON.marshal(data))
            if not operator_identity(raw, self.app_id, self.open_id, self.binding):
                return
            committed = threading.Event()
            self.events.put({"kind": "operator", "raw": raw, "committed": committed}, timeout=3)
            if not committed.wait(3):
                raise TimeoutError("飞书事件尚未完成本地持久化")

        def handle_card(data):
            raw = json.loads(lark.JSON.marshal(data))
            committed = threading.Event()
            self.events.put({"kind": "card", "raw": raw, "committed": committed}, timeout=3)
            if not committed.wait(3):
                raise TimeoutError("飞书卡片事件尚未完成本地持久化")
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                P2CardActionTriggerResponse,
            )
            return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "已提交，等待发送结果"}})

        ws_module.loop = asyncio.get_running_loop()
        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(handle)
                   .register_p2_card_action_trigger(handle_card).build())
        retries = 0
        while not self.stop.is_set():
            client = lark.ws.Client(self.app_id, self.secret, event_handler=handler,
                                   domain=lark.FEISHU_DOMAIN, auto_reconnect=False)
            baseline = asyncio.all_tasks()
            owned = set()
            try:
                await client._connect()
                owned.update(asyncio.all_tasks() - baseline)
                owned.add(asyncio.create_task(client._ping_loop()))
                self.online.set()
                retries = 0
                while not self.stop.is_set():
                    done, _ = await asyncio.wait(owned, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                    if done:
                        for task in done:
                            await task
                        raise ConnectionError("飞书接收连接已结束")
            except ClientException as exc:
                self.events.put({"kind": "feishu_error", "error": type(exc).__name__})
                break
            except Exception as exc:
                retries += 1
                self.events.put({"kind": "feishu_error", "error": type(exc).__name__})
            finally:
                self.online.clear()
                owned.update(asyncio.all_tasks() - baseline)
                for task in owned:
                    task.cancel()
                await asyncio.gather(*owned, return_exceptions=True)
                await client._disconnect()
            for _ in range(min(30, 2 ** min(retries, 5)) * 2):
                if self.stop.is_set():
                    break
                await asyncio.sleep(0.5)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=20)
        self.http.close()
