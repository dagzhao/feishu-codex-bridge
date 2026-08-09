"""飞书消息处理主逻辑与长连接服务入口。"""

import logging

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.ws import Client as WSClient

from .codex import run_codex
from .config import load_config, setup_logging
from .feishu import build_lark_client, extract_text, is_allowed, send_text

LOGGER = logging.getLogger("feishu_codex_bridge")


def handle_message(event: P2ImMessageReceiveV1, config: dict, client: lark.Client) -> None:
    chat_id = event.event.message.chat_id or ""
    prompt = extract_text(event)
    if not prompt:
        return
    if not is_allowed(event, config):
        send_text(client, chat_id, "❌ 你没有权限使用此机器人。")
        return

    send_text(client, chat_id, f"🧠 收到指令，Codex 开始处理：\n{prompt[:500]}")
    result = run_codex(
        prompt,
        config["codex_bin"],
        config["workdir"],
        int(config.get("task_timeout_sec", 300)),
    )
    max_len = int(config.get("max_reply_len", 3500))
    if len(result) > max_len:
        result = result[:max_len] + "\n\n…(结果过长已截断)"
    send_text(client, chat_id, result)


def make_handler(config: dict, client: lark.Client):
    def on_event(ctx, event: P2ImMessageReceiveV1):
        LOGGER.info("收到消息: message_id=%s", event.event.message.message_id)
        try:
            handle_message(event, config, client)
        except Exception as exc:
            LOGGER.exception("处理消息失败")
            try:
                send_text(client, event.event.message.chat_id, f"❌ 桥接服务出错：{exc}")
            except Exception:
                LOGGER.exception("发送错误提示失败")

    return on_event


def main() -> None:
    config = load_config()
    setup_logging(config)
    LOGGER.info("飞书桥接服务启动: app_id=%s", config["app_id"])
    client = build_lark_client(config)
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(make_handler(config, client))
        .build()
    )
    ws_client = WSClient(
        app_id=config["app_id"],
        app_secret=config["app_secret"],
        event_handler=handler,
    )
    ws_client.start()
