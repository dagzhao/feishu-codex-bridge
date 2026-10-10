"""飞书消息处理主逻辑与长连接服务入口。"""

import fcntl
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.ws import Client as WSClient

from .backend import BACKENDS, Backend, get_backend, run_task
from .config import load_config, setup_logging
from .feishu import (
    build_lark_client,
    extract_text,
    is_allowed,
    open_id_of,
    send_card,
    send_text,
)

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

_CLEAR_CMDS = ("新会话", "退出会话", "结束会话", "退出当前会话", "重置会话")

# 固定会话：所有飞书消息共用一个固定会话 id（不区分用户），映射存本地
# .feishu_sessions.json，**按后端分区**（claude / dsh 各存各的 id，切换
# 后端互不覆盖；旧版 flat 格式在首次读写时自动迁移，历史 dsh 会话指针
# 保留）。「新会话」只轮换当前后端的 id，下次对话走全新会话。会话文件
# （claude 在 ~/.claude/projects，dsh 在 ~/.dsh/sessions）由各自侧管理，
# 桥接层不删任何会话文件。
#
# 会话生命周期（2026-08-22 起）：默认**永不因空闲自动轮换**——只有
# 消息为「新会话」等命令时才轮换到全新会话 id。若在 config.json 显式
# 配置 session_idle_timeout_min > 0，则仍按该分钟数空闲自动轮换
# （可选兼容行为，默认 0 = 不轮换）。
_SESSION_MAP_FILE = Path(__file__).resolve().parent.parent / ".feishu_sessions.json"
_SESSIONS_KEY = "sessions"
# 旧版 flat 格式的键（一次性迁移用，见 _migrate_legacy_map）。
_SHARED_SESSION_KEY = "shared"
_SHARED_ACTIVE_KEY = "shared_active_at"

# 单实例 pidfile：防止误起多个桥接进程导致飞书消息重复处理。
_PID_FILE = Path(__file__).resolve().parent.parent / ".bridge.pid"
# 持有 flock 的文件对象（保持引用防止被 GC 关闭而释放锁）。
_PID_LOCK_FH = None

# 所有消息共用同一会话后，并发 run_task 会互相覆盖上下文，串行执行保证安全。
_RUN_LOCK = threading.Lock()


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


def _load_session_map() -> dict:
    """读取会话映射文件（缺失/损坏 → 空 dict）。"""
    try:
        data = json.loads(_SESSION_MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_session_map(data: dict) -> None:
    try:
        _SESSION_MAP_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        LOGGER.exception("保存会话映射失败")


def _migrate_legacy_map(data: dict) -> dict:
    """旧版 flat 格式（shared/shared_active_at）→ 按后端分区的新格式。

    按各后端的 id 格式把旧共用 id 归入对应分区（例如
    ``session-feishu-xxx`` 归 dsh 分区，UUID 归 claude 分区）；无法识别
    则丢弃该 id（等同新会话）。已有 ``sessions`` 键则原样返回。
    """
    if _SESSIONS_KEY in data:
        return data
    sessions: dict = {}
    sid = data.get(_SHARED_SESSION_KEY)
    if sid:
        for backend in BACKENDS.values():
            if backend.looks_like(str(sid)):
                sessions[backend.name] = {
                    "id": str(sid),
                    "active_at": data.get(_SHARED_ACTIVE_KEY),
                }
                break
    return {_SESSIONS_KEY: sessions}


def _shared_session_id(backend: Backend, idle_timeout_sec: Optional[float] = None) -> str:
    """读取（或创建）当前后端共用的固定会话 id。

    idle_timeout_sec 非空时：距上次消息超过该时长 → 自动轮换到全新会话 id
    （旧会话结束）；未超时 → 沿用并刷新 active_at。为 None 则永不因空闲
    轮换（只有「新会话」命令强制重置）。分区内格式不符当前后端的脏数据
    视为无效并重建。调用方需持有 _RUN_LOCK（同一会话不能并发写）。
    """
    data = _migrate_legacy_map(_load_session_map())
    sessions = data.setdefault(_SESSIONS_KEY, {})
    now = time.time()
    entry = sessions.get(backend.name)
    sid = None
    last = None
    if isinstance(entry, dict):
        sid = entry.get("id")
        last = entry.get("active_at")
    if sid and not backend.looks_like(str(sid)):
        LOGGER.warning("会话 id %r 不符合 %s 后端格式，视为无效并重建", sid, backend.name)
        sid = None
    if sid and idle_timeout_sec and last is not None:
        try:
            idle = now - float(last)
        except (TypeError, ValueError):
            idle = 0.0
        if idle > idle_timeout_sec:
            LOGGER.info("会话空闲 %.0f 秒（>%.0f），自动开启新会话", idle, idle_timeout_sec)
            sid = None
    if not sid:
        sid = backend.new_session_id()
    sessions[backend.name] = {"id": sid, "active_at": now}
    _save_session_map(data)
    return sid


def _reset_shared_session(backend: Backend) -> None:
    """重置当前后端的共用会话映射（轮换 id）；其它后端分区保留。

    旧会话文件（claude 在 ~/.claude/projects，dsh 在 ~/.dsh/sessions）
    由各自侧管理，桥接层不删任何会话文件。
    """
    data = _migrate_legacy_map(_load_session_map())
    sessions = data.get(_SESSIONS_KEY) or {}
    sessions.pop(backend.name, None)
    data[_SESSIONS_KEY] = sessions
    _save_session_map(data)


def handle_commands(prompt: str, backend: Backend) -> Optional[str]:
    """处理会话管理命令；返回回复文本，非命令消息返回 None。

    「新会话」删除当前后端的共用会话映射（轮换 id），下次对话走全新
    上下文；旧会话文件由各自后端侧保留，桥接层不删任何会话。
    """
    prompt = prompt.strip()
    if prompt in _CLEAR_CMDS:
        _reset_shared_session(backend)
        return "🆕 已开启新会话：下次对话将使用全新上下文。"
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

    backend = get_backend(config)
    open_id = open_id_of(event)
    command_reply = handle_commands(prompt, backend)
    if command_reply is not None:
        LOGGER.info("命令处理: open_id=%s 命令=%s", open_id, prompt.strip()[:50])
        send_text(client, chat_id, command_reply)
        return

    send_text(client, chat_id, f"🧠 收到指令，{backend.label} 开始处理：\n{prompt[:500]}")

    def _send_progress(text: str) -> None:
        try:
            send_text(client, chat_id, text)
        except Exception:
            LOGGER.exception("发送过程提示失败")

    # 所有消息共用同一会话，串行执行避免并发写入互相覆盖上下文。
    # 默认永不因空闲自动轮换（session_idle_timeout_min 配置 > 0 时才按
    # 分钟数空闲轮换）；只有「新会话」等命令才强制轮换全新上下文。
    idle_min = float(config.get("session_idle_timeout_min", 0) or 0)
    idle_sec = idle_min * 60 if idle_min > 0 else None
    with _RUN_LOCK:
        result = run_task(
            backend,
            config,
            prompt,
            workdir=config["workdir"],
            timeout=int(config.get("task_timeout_sec", 300)),
            on_progress=_send_progress,
            progress_interval=float(config.get("progress_interval_sec", 10)),
            session_id=_shared_session_id(backend, idle_timeout_sec=idle_sec),
        )
    max_len = int(config.get("max_reply_len", 3500))
    if len(result) > max_len:
        result = result[:max_len] + "\n\n…(结果过长已截断)"
    send_card(client, chat_id, result, title=f"🤖 {backend.label} 执行结果")


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


def _acquire_single_instance() -> None:
    """单实例保护：flock 原子锁 + pidfile，防止误起多个桥接进程。

    锁随进程持有（fd 保存在 _PID_LOCK_FH），第二个进程拿不到锁即退出，
    无 TOCTOU 竞态。
    """
    global _PID_LOCK_FH
    try:
        fh = open(_PID_FILE, "a+", encoding="utf-8")
    except OSError:
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        LOGGER.error("另一个桥接进程已在运行，拒绝重复启动")
        raise SystemExit(1)
    _PID_LOCK_FH = fh  # 保持引用，锁随进程生命周期持有
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()


def main() -> None:
    config = load_config()
    setup_logging(config)
    _acquire_single_instance()
    LOGGER.info("飞书桥接服务启动: app_id=%s backend=%s", config["app_id"], config.get("backend"))
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
