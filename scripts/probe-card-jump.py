"""向已绑定本人私聊发送独立卡片和引用提醒，用于人工验证跳转及草稿。"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from goofish_bridge.config import Config
from goofish_bridge.feishu_adapter import (
    build_customer_card,
    configure_sdk_direct,
    credentials,
    load_binding,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--separate-from", type=Path, help="复用已有测试记录的原卡片，插入分隔消息后再测试")
    args = parser.parse_args()
    config = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")
    app_id, secret, open_id = credentials(config)
    binding = load_binding(config)
    if binding["app_id"] != app_id or binding["open_id"] != open_id:
        raise ValueError("飞书配置与本人绑定不一致")
    previous_message_id = None
    if args.separate_from:
        source = args.separate_from.resolve()
        if source.parent != config.root / "data" or not source.name.startswith("card-jump-probe-"):
            raise ValueError("仅允许复用本项目 data 下的卡片测试记录")
        previous = json.loads(source.read_text(encoding="utf-8"))
        previous_message_id = next((item.get("message_id") for item in previous["deliveries"]
                                    if item["label"] == "独立测试卡片" and item["state"] == "SERVER_ACCEPTED"), None)
        if not previous_message_id:
            raise ValueError("记录中没有成功发送的原测试卡片")
    run_id = uuid4().hex
    record_path = config.root / "data" / f"card-jump-probe-{run_id}.json"
    record = {"run_id": run_id, "client_result": "PENDING", "deliveries": []}

    def save():
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def send(request, method, label):
        delivery = {"label": label, "state": "DISPATCHING"}
        record["deliveries"].append(delivery)
        save()
        try:
            response = method(request)
            if not response.success():
                delivery.update(state="FAILED", code=response.code)
                raise RuntimeError(f"{label}被拒绝，接口代码 {response.code}")
            message_id = getattr(response.data, "message_id", None)
            if not message_id:
                raise RuntimeError("成功回包缺少消息 ID，不自动重发")
            delivery.update(state="SERVER_ACCEPTED", message_id=message_id)
            print(f"{label}：飞书已接受。")
            return message_id
        except Exception:
            if delivery["state"] == "DISPATCHING":
                delivery["state"] = "UNKNOWN"
            raise
        finally:
            save()

    http = configure_sdk_direct()
    try:
        client = lark.Client.builder().app_id(app_id).app_secret(secret).domain(lark.FEISHU_DOMAIN).timeout(15).build()
        card = build_customer_card({"account_name": "独立测试", "customer_name": "跳转测试客户",
                                    "entries": [{"speaker": "测试说明", "text": "在下方输入草稿测试123，不要提交；随后点击下一条提醒的按钮，观察是否定位回本卡片且草稿仍在。"}]})
        card["header"]["title"]["content"] = "定位测试原卡片"
        card["elements"].pop(1)
        card["elements"][-1]["elements"][-1]["disabled"] = True
        card["elements"][-1]["elements"][-1]["text"]["content"] = "仅测试草稿，不发送"
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder().receive_id(binding["chat_id"]).msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False)).uuid(uuid4().hex).build()).build()
        message_id = previous_message_id or send(request, client.im.v1.message.create, "独立测试卡片")
        if previous_message_id:
            record["source_record"] = args.separate_from.name
            record["target_message_id"] = message_id
            # 分隔内容只发一条卡片，逐行显示，避免多条提醒刷屏。
            spacer = {"header": {"template": "grey", "title": {"tag": "plain_text", "content": "跳转测试分隔区"}},
                      "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": f"分隔行 {index:02d} · 用于把原测试卡片移出当前屏幕"}}
                                   for index in range(1, 31)]}
            request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
                CreateMessageRequestBody.builder().receive_id(binding["chat_id"]).msg_type("interactive")
                .content(json.dumps(spacer, ensure_ascii=False)).uuid(uuid4().hex).build()).build()
            send(request, client.im.v1.message.create, "测试分隔卡片")
        # messageId 未列在官方聊天协议参数中，仅作为待人工验证的候选链接。
        link = "https://applink.feishu.cn/client/chat/open?" + urlencode({
            "openChatId": binding["chat_id"], "messageId": message_id})
        record["candidate_link"] = link
        reminder = {"header": {"template": "orange", "title": {"tag": "plain_text", "content": "新消息提醒 · 跳转测试"}},
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": "先在上一张测试卡片输入草稿测试123，然后点下面按钮。若没有定位到原卡片，再点本消息上方的引用区域作对照。请回到 Codex 告知：按钮能否定位、引用能否定位、草稿是否保留。"}},
                        {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看客户卡片（测试）"}, "url": link, "type": "primary"}]}]}
        if previous_message_id:
            reminder["header"]["title"]["content"] = "第二轮定位测试 · 已隔开原卡片"
            reminder["elements"][0]["text"]["content"] = "先停留在本提醒处，确认原卡片不在当前屏幕，再点击下面按钮。只有跳回带输入框的『定位测试原卡片』才算定位成功。若无跳转，再点击本消息上方的引用区域作对照，并查看原草稿是否还在。"
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("interactive")
            .content(json.dumps(reminder, ensure_ascii=False)).uuid(uuid4().hex).build()).build()
        send(request, client.im.v1.message.reply, "引用测试提醒")
        print("客户端跳转与草稿结果：待人工确认。未写入业务路由。")
    finally:
        http.close()


if __name__ == "__main__":
    main()
