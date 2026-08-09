import json

import pytest
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu_codex_bridge.feishu import extract_text, is_allowed


def make_event(text: str, open_id: str = "ou_test", chat_id: str = "oc_test"):
    return P2ImMessageReceiveV1(
        {
            "event": {
                "sender": {"sender_id": {"open_id": open_id}},
                "message": {
                    "message_id": "om_test",
                    "chat_id": chat_id,
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            }
        }
    )


def test_extract_text_strips_mention():
    event = make_event('<at user_id="ou_test"></at> 帮我查一下路由器状态')
    assert extract_text(event) == "帮我查一下路由器状态"


def test_extract_text_plain():
    event = make_event("查询系统状态")
    assert extract_text(event) == "查询系统状态"


def test_extract_text_empty():
    event = make_event("   ")
    assert extract_text(event) == ""


def test_is_allowed_whitelisted_user():
    event = make_event("x", open_id="ou_owner")
    config = {"allowed_open_ids": ["ou_owner"], "allowed_chat_ids": []}
    assert is_allowed(event, config) is True


def test_is_allowed_rejects_unknown_user():
    event = make_event("x", open_id="ou_stranger")
    config = {"allowed_open_ids": ["ou_owner"], "allowed_chat_ids": []}
    assert is_allowed(event, config) is False


def test_is_allowed_whitelisted_chat():
    event = make_event("x", open_id="ou_stranger", chat_id="oc_family")
    config = {"allowed_open_ids": [], "allowed_chat_ids": ["oc_family"]}
    assert is_allowed(event, config) is True


def test_handle_empty_prompt_returns_without_sending():
    from feishu_codex_bridge.bridge import handle_message

    sent = []

    def fake_send(client, chat_id, text):
        sent.append(text)

    event = make_event("  ")
    handle_message(event, {"codex_bin": "", "workdir": ""}, object())
    assert sent == []
