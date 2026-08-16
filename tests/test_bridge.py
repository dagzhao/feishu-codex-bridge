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


def test_is_allowed_empty_lists_allows_everyone():
    event = make_event("x", open_id="ou_stranger")
    config = {"allowed_open_ids": [], "allowed_chat_ids": []}
    assert is_allowed(event, config) is True


def test_handle_empty_prompt_returns_without_sending():
    from feishu_codex_bridge.bridge import handle_message

    sent = []

    def fake_send(client, chat_id, text):
        sent.append(text)

    event = make_event("  ")
    handle_message(event, {"dsh_bin": "", "workdir": ""}, object())
    assert sent == []


def test_mark_processed_deduplicates():
    from feishu_codex_bridge.bridge import _mark_processed, _processed_messages

    _processed_messages.clear()
    assert _mark_processed("om_dup") is False
    assert _mark_processed("om_dup") is True
    assert _mark_processed("om_other") is False


def test_handle_commands_clear_acknowledges_one_shot_mode():
    from feishu_codex_bridge.bridge import handle_commands

    reply = handle_commands("新会话", "ou_1")
    assert "一次性任务" in reply
    reply = handle_commands("退出会话", "ou_1")
    assert "一次性任务" in reply
    assert handle_commands("查看会话", "ou_1") is None
    assert handle_commands("查询系统状态", "ou_1") is None


def test_list_recent_sessions_reads_files(tmp_path, monkeypatch):
    from feishu_codex_bridge.sessions import latest_session_id, list_recent_sessions

    home = tmp_path / "codex"
    session_dir = home / "sessions" / "2026" / "08" / "09"
    session_dir.mkdir(parents=True)
    path = session_dir / "rollout-2026-08-09T13-56-04-019fe517-b8c1-7ec0-bde7-d043fd72cddd.jsonl"
    path.write_text(
        json.dumps(
            {"type": "session_meta", "payload": {"session_id": "019fe517-b8c1-7ec0-bde7-d043fd72cddd"}}
        )
        + "\n"
        + json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "查询系统状态"}}
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    items = list_recent_sessions(5)
    assert len(items) == 1
    assert items[0]["id"] == "019fe517-b8c1-7ec0-bde7-d043fd72cddd"
    assert items[0]["title"] == "查询系统状态"

    exec_path = session_dir / "rollout-2026-08-09T14-00-07-019fe51b-6d58-7af2-bda8-0e385e0ec668.jsonl"
    exec_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "session_id": "019fe51b-6d58-7af2-bda8-0e385e0ec668",
                    "source": "exec",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before_exec = exec_path.stat().st_mtime

    dup_path = session_dir / "rollout-2026-08-09T14-05-00-019fe517-b8c1-7ec0-bde7-d043fd72cddd.jsonl"
    dup_path.write_text(
        json.dumps(
            {"type": "session_meta", "payload": {"session_id": "019fe517-b8c1-7ec0-bde7-d043fd72cddd"}}
        )
        + "\n"
        + json.dumps(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "最新标题"}}
        )
        + "\n",
        encoding="utf-8",
    )
    dup_ids = [item["id"] for item in list_recent_sessions(5)]
    assert len(dup_ids) == len(set(dup_ids))

    assert all(item["id"] != "019fe51b-6d58-7af2-bda8-0e385e0ec668" for item in list_recent_sessions(5))
    assert latest_session_id(after=before_exec) == "019fe517-b8c1-7ec0-bde7-d043fd72cddd"
    assert latest_session_id() == "019fe517-b8c1-7ec0-bde7-d043fd72cddd"


def test_register_in_desktop_index(tmp_path, monkeypatch):
    from feishu_codex_bridge.sessions import register_in_desktop_index

    home = tmp_path / "codex"
    home.mkdir()
    index = home / "session_index.jsonl"
    index.write_text(
        json.dumps({"id": "019f-old", "thread_name": "旧会话", "updated_at": "2026-08-09T00:00:00Z"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))

    assert register_in_desktop_index("019f-new", "新会话的第一条消息") is True
    assert register_in_desktop_index("019f-new", "再次登记") is False
    lines = index.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["id"] == "019f-new"
    assert json.loads(lines[1])["thread_name"] == "新会话的第一条消息"


def test_remember_chat_persists(tmp_path, monkeypatch):
    from feishu_codex_bridge import bridge
    from feishu_codex_bridge.bridge import _load_last_chat_id, _remember_chat

    monkeypatch.setattr("feishu_codex_bridge.bridge._STATE_FILE", tmp_path / "state.json")
    _remember_chat("oc_test_123")
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == '{"last_chat_id": "oc_test_123"}'
    assert _load_last_chat_id() == "oc_test_123"


def test_notify_connection_short_downtime(monkeypatch):
    from feishu_codex_bridge import bridge

    sent = []
    monkeypatch.setattr(bridge, "_send_notice", lambda client, text: sent.append(text))
    monkeypatch.setattr(bridge, "_drop_started_at", __import__("time").monotonic() - 5)
    bridge._notify_connection(None)
    assert sent == []


def test_notify_connection_long_downtime(monkeypatch):
    from feishu_codex_bridge import bridge

    sent = []
    monkeypatch.setattr(bridge, "_send_notice", lambda client, text: sent.append(text))
    monkeypatch.setattr(bridge, "_drop_started_at", __import__("time").monotonic() - 125)
    bridge._notify_connection(None)
    assert len(sent) == 1
    assert "125" in sent[0]


def test_warn_if_still_down_sends_once(monkeypatch):
    from feishu_codex_bridge import bridge

    sent = []
    monkeypatch.setattr(bridge, "_send_notice", lambda client, text: sent.append(text))
    monkeypatch.setattr(bridge, "_LONG_DISCONNECT_WARN_SEC", 0.05)
    monkeypatch.setattr(bridge, "_reconnecting", True)
    monkeypatch.setattr(bridge, "_warned_long_disconnect", False)

    import threading
    t = threading.Thread(target=bridge._warn_if_still_down, args=(None,))
    t.start()
    t.join(timeout=2)
    assert len(sent) == 1
    assert "1 分钟" in sent[0]
    assert bridge._warned_long_disconnect is True

    # 已提醒过就不再重复发
    t2 = threading.Thread(target=bridge._warn_if_still_down, args=(None,))
    t2.start()
    t2.join(timeout=2)
    assert len(sent) == 1
