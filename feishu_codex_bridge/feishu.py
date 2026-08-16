"""飞书客户端封装：消息解析、权限判断、消息发送。"""

import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

LOGGER = logging.getLogger("feishu_codex_bridge.feishu")


def build_lark_client(config: dict) -> lark.Client:
    return (
        lark.Client.builder()
        .app_id(config["app_id"])
        .app_secret(config["app_secret"])
        .log_level(lark.LogLevel.INFO)
        .build()
    )


def _open_id_of(event: P2ImMessageReceiveV1) -> str:
    try:
        return event.event.sender.sender_id.open_id or ""
    except AttributeError:
        return ""


def open_id_of(event: P2ImMessageReceiveV1) -> str:
    return _open_id_of(event)


def is_allowed(event: P2ImMessageReceiveV1, config: dict) -> bool:
    open_id = _open_id_of(event)
    chat_id = event.event.message.chat_id or ""
    allow_ids = set(config.get("allowed_open_ids", []))
    allow_chats = set(config.get("allowed_chat_ids", []))
    if (not allow_ids and not allow_chats) or open_id in allow_ids or chat_id in allow_chats:
        return True
    LOGGER.warning("拒绝未授权消息: open_id=%s chat_id=%s", open_id, chat_id)
    return False


def extract_text(event: P2ImMessageReceiveV1) -> str:
    try:
        raw = event.event.message.content or ""
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    text = str(parsed.get("text", "")).strip()
    # 去掉飞书 @ 机器人的占位符 <at user_id="..."></at>
    while "<at " in text:
        start = text.find("<at ")
        end = text.find(">", start)
        if end == -1:
            break
        tag_end = text.find("</at>", end)
        if tag_end == -1:
            break
        text = text[:start] + text[tag_end + len("</at>"):]
    return text.strip()


def send_text(client: lark.Client, chat_id: str, text: str) -> None:
    content = json.dumps({"text": text}, ensure_ascii=False)
    request = (
        lark.im.v1.CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        LOGGER.error("发送消息失败: code=%s msg=%s", response.code, response.msg)
