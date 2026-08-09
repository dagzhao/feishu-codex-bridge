#!/usr/bin/env python3
"""飞书 <-> Codex 桥接服务

通过飞书机器人（官方长连接模式）接收消息，调用本机 codex exec 执行，
并把 Codex 的结果发回飞书。
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import lark_oapi as lark
from lark_oapi.ws import Client as WSClient
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOGGER = logging.getLogger("feishu_codex_bridge")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    app_id = cfg.get("app_id", "")
    if app_id.startswith("cli_") and len(app_id) > 10:
        return cfg
    raise SystemExit("config.json 中的 app_id 尚未填写，请先按 README 配置飞书应用。")


CONFIG = load_config()
LARK_CLIENT = (
    lark.Client.builder()
    .app_id(CONFIG["app_id"])
    .app_secret(CONFIG["app_secret"])
    .log_level(lark.LogLevel.INFO)
    .build()
)


def setup_logging() -> None:
    log_path = BASE_DIR / CONFIG.get("log_file", "bridge.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def is_allowed(event: P2ImMessageReceiveV1) -> bool:
    open_id = ""
    try:
        open_id = event.event.sender.sender_id.open_id or ""
    except AttributeError:
        pass
    chat_id = event.event.message.chat_id or ""
    allow_ids = set(CONFIG.get("allowed_open_ids", []))
    allow_chats = set(CONFIG.get("allowed_chat_ids", []))
    if open_id in allow_ids or chat_id in allow_chats:
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


def send_text(chat_id: str, text: str) -> None:
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
    response = LARK_CLIENT.im.v1.message.create(request)
    if not response.success():
        LOGGER.error("发送消息失败: code=%s msg=%s", response.code, response.msg)


def run_codex(prompt: str) -> str:
    codex_bin = CONFIG["codex_bin"]
    workdir = CONFIG["workdir"]
    timeout = int(CONFIG.get("task_timeout_sec", 300))
    cmd = [
        codex_bin, "exec", "--skip-git-repo-check",
        "-c", "sandbox_mode=danger-full-access",
        "-c", "approval_policy=never",
        prompt,
    ]
    LOGGER.info("调用 codex: %s", prompt[:200])
    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"⚠️ Codex 任务超过 {timeout} 秒未完成，已终止。可以换更简单的指令重试。"
    output = (result.stdout or "").strip()
    if not output:
        output = (result.stderr or "").strip()
    if result.returncode != 0:
        return f"❌ Codex 执行失败（退出码 {result.returncode}）：\n{output[-1500:]}"
    return output


def handle_message(event: P2ImMessageReceiveV1) -> None:
    chat_id = event.event.message.chat_id or ""
    prompt = extract_text(event)
    if not prompt:
        return
    if not is_allowed(event):
        send_text(chat_id, "❌ 你没有权限使用此机器人。")
        return

    send_text(chat_id, f"🧠 收到指令，Codex 开始处理：\n{prompt[:500]}")
    result = run_codex(prompt)
    max_len = int(CONFIG.get("max_reply_len", 3500))
    if len(result) > max_len:
        result = result[:max_len] + "\n\n…(结果过长已截断)"
    send_text(chat_id, result)


def on_event(ctx, event: P2ImMessageReceiveV1):
    LOGGER.info("收到消息: message_id=%s", event.event.message.message_id)
    try:
        handle_message(event)
    except Exception as exc:
        LOGGER.exception("处理消息失败")
        try:
            send_text(event.event.message.chat_id, f"❌ 桥接服务出错：{exc}")
        except Exception:
            LOGGER.exception("发送错误提示失败")
    return


def main() -> None:
    setup_logging()
    LOGGER.info("飞书桥接服务启动: app_id=%s", CONFIG["app_id"])
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_event)
        .build()
    )
    ws_client = WSClient(
        app_id=CONFIG["app_id"],
        app_secret=CONFIG["app_secret"],
        event_handler=handler,
    )
    ws_client.start()


if __name__ == "__main__":
    main()
