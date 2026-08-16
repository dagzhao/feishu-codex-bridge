"""飞书消息处理主逻辑与长连接服务入口。"""

import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.ws import Client as WSClient

from .dsh import run_dsh
from .config import load_config, setup_logging
from .feishu import build_lark_client, extract_text, is_allowed, open_id_of, send_text

LOGGER = logging.getLogger("feishu_codex_bridge")

_processed_messages: "OrderedDict[str, float]" = OrderedDict()
_DEDUPE_TTL_SEC = 600

_MIN_NOTIFY_DOWNTIME_SEC = 15.0
_STATE_FILE = Path(__file__).resolve().parent.parent / ".bridge_state.json"

_last_chat_id: Optional[str] = None
_drop_started_at: Optional[float] = None
_reconnecting: bool = False
_warned_long_disconnect: bool = False
_LONG_DISCONNECT_WARN_SEC = 60.0

_CLEAR_CMDS = ("新会话", "退出会话", "结束会话", "退出当前会话")


def _load_last_chat_id() -> Optional[str]:
    """读取最近聊天的 chat_id（持久化在本地状态文件）。"""
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return data.get("last_chat_id") or None
    except (OSError, json.JSONDecodeError):
        return None


def _remember_chat(chat_id: str) -> None:
    """记录最近聊天的 chat_id，用于连接状态通知。"""
    global _last_chat_id
    if not chat_id:
        return
    _last_chat_id = chat_id
    try:
        _STATE_FILE.write_text(
            json.dumps({"last_chat_id": chat_id}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        LOGGER.exception("保存最近会话失败")


def _send_notice(client: lark.Client, text: str) -> None:
    """向最近聊天的会话发送连接状态通知（无会话记录时跳过）。"""
    chat_id = _last_chat_id or _load_last_chat_id()
    if not chat_id:
        LOGGER.info("没有可通知的会话，跳过连接状态通知")
        return
    try:
        send_text(client, chat_id, text)
    except Exception:
        LOGGER.exception("发送连接状态通知失败")


def _notify_connection(client: lark.Client) -> None:
    """断线恢复后按需通知用户，避免消息悄悄丢失而不自知。"""
    downtime = time.monotonic() - _drop_started_at if _drop_started_at else 0.0
    secs = int(downtime)
    if secs < _MIN_NOTIFY_DOWNTIME_SEC:
        LOGGER.info("连接仅中断 %s 秒，不打扰用户", secs)
        return
    _send_notice(
        client,
        f"🔌 飞书连接刚恢复（中断约 {secs} 秒）。\n"
        "如果刚才发的消息没有收到回复，请重发一次。",
    )


def _warn_if_still_down(client: lark.Client) -> None:
    """断线超过 1 分钟仍未恢复时，主动提醒用户（只提醒一次）。"""
    time.sleep(_LONG_DISCONNECT_WARN_SEC)
    global _warned_long_disconnect
    if _reconnecting and not _warned_long_disconnect:
        _warned_long_disconnect = True
        _send_notice(
            client,
            "⚠️ 飞书连接已断开超过 1 分钟，正在自动重连…\n"
            "如果刚发了消息，可能没收到；恢复后我会通知你。",
        )



def _mark_processed(message_id: str) -> bool:
    """记录消息 ID；返回 True 表示该消息已处理过（重复投递）。"""
    now = time.time()
    while _processed_messages and next(iter(_processed_messages.values())) < now - _DEDUPE_TTL_SEC:
        _processed_messages.popitem(last=False)
    if message_id in _processed_messages:
        return True
    _processed_messages[message_id] = now
    return False


def handle_commands(prompt: str, open_id: str) -> Optional[str]:
    """处理会话管理命令；返回回复文本，非命令消息返回 None。

    DSH headless 为一次性任务模式：每条消息都是独立任务，没有可续聊的
    会话。保留「新会话」等命令仅为提示用户这一行为。
    """
    prompt = prompt.strip()
    if prompt in _CLEAR_CMDS:
        return "🆕 已开启新会话：DSH 为一次性任务模式，每条消息都会独立处理，无需切换会话。"
    return None


def handle_message(event: P2ImMessageReceiveV1, config: dict, client: lark.Client) -> None:
    chat_id = event.event.message.chat_id or ""
    prompt = extract_text(event)
    if not prompt:
        return
    if not is_allowed(event, config):
        send_text(client, chat_id, "❌ 你没有权限使用此机器人。")
        return
    _remember_chat(chat_id)

    open_id = open_id_of(event)
    command_reply = handle_commands(prompt, open_id)
    if command_reply is not None:
        LOGGER.info("命令处理: open_id=%s 命令=%s", open_id, prompt.strip()[:50])
        send_text(client, chat_id, command_reply)
        return

    send_text(client, chat_id, f"🧠 收到指令，DSH 开始处理：\n{prompt[:500]}")

    def _send_progress(text: str) -> None:
        try:
            send_text(client, chat_id, text)
        except Exception:
            LOGGER.exception("发送过程提示失败")

    result = run_dsh(
        prompt,
        config["dsh_bin"],
        config["workdir"],
        int(config.get("task_timeout_sec", 300)),
        on_progress=_send_progress,
        progress_interval=float(config.get("progress_interval_sec", 10)),
    )
    max_len = int(config.get("max_reply_len", 3500))
    if len(result) > max_len:
        result = result[:max_len] + "\n\n…(结果过长已截断)"
    send_text(client, chat_id, result)


def make_handler(config: dict, client: lark.Client):
    def on_event(event: P2ImMessageReceiveV1):
        message_id = event.event.message.message_id
        if _mark_processed(message_id):
            LOGGER.info("跳过重复投递: message_id=%s", message_id)
            return
        LOGGER.info("收到消息: message_id=%s", message_id)
        threading.Thread(
            target=_safe_handle, args=(event, config, client), daemon=True
        ).start()

    return on_event


def _safe_handle(event: P2ImMessageReceiveV1, config: dict, client: lark.Client) -> None:
    """后台线程处理消息，让事件确认立即返回，避免飞书重试导致重复执行。"""
    try:
        handle_message(event, config, client)
    except Exception as exc:
        LOGGER.exception("处理消息失败")
        try:
            send_text(client, event.event.message.chat_id, f"❌ 桥接服务出错：{exc}")
        except Exception:
            LOGGER.exception("发送错误提示失败")


def main() -> None:
    config = load_config()
    setup_logging(config)
    LOGGER.info("飞书桥接服务启动: app_id=%s", config["app_id"])
    client = build_lark_client(config)

    def _on_reconnecting() -> None:
        global _drop_started_at, _reconnecting, _warned_long_disconnect
        _drop_started_at = time.monotonic()
        _reconnecting = True
        _warned_long_disconnect = False
        LOGGER.warning("飞书连接断开，开始自动重连")
        threading.Thread(
            target=_warn_if_still_down, args=(client,), daemon=True
        ).start()

    def _on_reconnected() -> None:
        global _reconnecting
        _reconnecting = False
        LOGGER.info("飞书连接已恢复")
        try:
            threading.Thread(
                target=_notify_connection, args=(client,), daemon=True
            ).start()
        except Exception:
            LOGGER.exception("连接恢复通知失败")

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
    ws_client.on_reconnecting = _on_reconnecting
    ws_client.on_reconnected = _on_reconnected
    if _load_last_chat_id():
        threading.Thread(
            target=lambda: _send_notice(client, "✅ 桥接服务已启动，正在连接飞书…"),
            daemon=True,
        ).start()
    ws_client.start()
