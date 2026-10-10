"""飞书客户端封装：消息解析、权限判断、消息发送。"""

import json
import logging
import re

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


def md_table_to_codeblock(text: str) -> str:
    """把 Markdown 表格块转成等宽代码块（飞书卡片 markdown 不支持表格）。

    识别连续的 `| ... |` 行（含 `|---|` 分隔行），整体包进 ``` 代码块，
    使表格在飞书卡片里以等宽字体对齐显示。
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and line.count("|") >= 2:
            block = [line]
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("|") and lines[j].count("|") >= 2:
                block.append(lines[j])
                j += 1
            out.append("```\n" + "\n".join(block) + "\n```")
            i = j
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _split_md_blocks(text: str) -> list:
    """把文本切成 (kind, data) 块：kind 为 'text' 或 'table'。

    table 的 data 是行列表，每行是单元格列表（去掉分隔行 `|---|`）。
    """
    lines = text.splitlines()
    blocks: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and line.count("|") >= 2:
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|") and lines[i].count("|") >= 2:
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                # 跳过 `| --- | --- |` 分隔行
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(("table", rows))
        else:
            buf = [line]
            i += 1
            while i < len(lines) and not (lines[i].lstrip().startswith("|") and lines[i].count("|") >= 2):
                buf.append(lines[i])
                i += 1
            blocks.append(("text", "\n".join(buf)))
    return blocks


def _table_to_column_set(rows: list) -> dict:
    """把表格行转成飞书卡片 column_set 组件（多列并排，表头加粗、斑马纹）。"""
    ncols = max(len(r) for r in rows)
    # 每列一个 column，纵向排列该列所有单元格；表头行用加粗
    columns = []
    for ci in range(ncols):
        elements = []
        for ri, row in enumerate(rows):
            cell = row[ci] if ci < len(row) else ""
            content = cell
            if ri == 0 and cell:
                content = f"**{cell}**"
            elif not cell:
                content = "-"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
        columns.append({
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "vertical_align": "top",
            "elements": elements,
        })
    return {"tag": "column_set", "flex_mode": "none", "columns": columns}


def _md_to_card_elements(text: str) -> list:
    """把 markdown 文本转成卡片 elements：文本→markdown 元素，表格→column_set。"""
    elements = []
    for kind, data in _split_md_blocks(text):
        if kind == "table":
            elements.append(_table_to_column_set(data))
        else:
            content = data.strip()
            if content:
                elements.append({"tag": "markdown", "content": content})
    return elements or [{"tag": "markdown", "content": text}]


def send_card(client: lark.Client, chat_id: str, text: str, title: str = "🤖 DSH 执行结果") -> None:
    """用飞书消息卡片发送内容，markdown 会被渲染（表格转 column_set 多列组件）。

    - 卡片 markdown 支持：加粗、行内代码、列表、链接、引用、标题。
    - 表格不支持 → 转成原生 column_set 组件（表头加粗、多列并排）。
    - 简单消息（无 markdown 特征）退化为纯文本发送。
    - title 为卡片标题（调用方按当前后端传 "🤖 Claude/DSH 执行结果"）。
    """
    elements = _md_to_card_elements(text)
    has_md = any(
        e["tag"] == "column_set" or any(seg in e.get("content", "") for seg in ("**", "`", "#", "- ", "* ", "> "))
        for e in elements
    )
    if not has_md and all(e["tag"] == "markdown" for e in elements):
        send_text(client, chat_id, text)
        return
    # content 只放 card 对象本身（不含 msg_type；msg_type 在请求体独立字段）
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": elements,
    }
    content = json.dumps(card, ensure_ascii=False)
    request = (
        lark.im.v1.CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        LOGGER.error("发送卡片失败: code=%s msg=%s", response.code, response.msg)
