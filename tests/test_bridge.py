import json
import os
import time
from types import SimpleNamespace

import pytest
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from feishu_codex_bridge.feishu import extract_text, is_allowed, md_table_to_codeblock, send_card


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


def test_md_table_to_codeblock_wraps_table():
    text = "标题\n\n| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n\n结尾"
    out = md_table_to_codeblock(text)
    assert "```" in out
    assert "| 列A | 列B |" in out
    # 表格被整体包进一个代码块
    assert out.count("```") == 2
    assert out.index("| 列A | 列B |") > out.index("```")


def test_md_table_to_codeblock_leaves_plain_text():
    text = "纯文本，没有表格"
    assert md_table_to_codeblock(text) == text


def test_send_card_sends_interactive_for_markdown(monkeypatch):
    import feishu_codex_bridge.feishu as feishu_mod

    captured = {}

    class RequestBody:
        def __init__(self):
            self.values = {}

        def receive_id(self, v):
            self.values["receive_id"] = v
            return self

        def msg_type(self, v):
            self.values["msg_type"] = v
            return self

        def content(self, v):
            self.values["content"] = v
            return self

        def build(self):
            return SimpleNamespace(**self.values)

    class RequestBuilder:
        def __init__(self):
            self._body = None

        def receive_id_type(self, _):
            return self

        def request_body(self, body):
            self._body = body
            return self

        def build(self):
            return {"request_body": self._body}

    class FakeMessage:
        def create(self, request):
            captured["request"] = request
            return type("R", (), {"success": lambda self: True, "code": 0, "msg": "ok"})()

    fake_lark = type(
        "lark",
        (),
        {"im": type("im", (), {"v1": type("v1", (), {
            "CreateMessageRequest": type("C", (), {"builder": staticmethod(RequestBuilder)}),
            "CreateMessageRequestBody": type("B", (), {"builder": staticmethod(RequestBody)}),
        })()})()},
    )
    monkeypatch.setattr(feishu_mod, "lark", fake_lark)
    client = type("C", (), {"im": type("im", (), {"v1": type("v1", (), {
        "message": FakeMessage()})()})()})()

    send_card(client, "oc_1", "**加粗** 和 `代码`")
    body = captured["request"]["request_body"]
    assert body.msg_type == "interactive"
    payload = json.loads(body.content)
    # content 只含 card 对象本身（无 msg_type / card 包装）
    assert "msg_type" not in payload
    assert payload["elements"][0]["tag"] == "markdown"
    assert payload["header"]["title"]["content"] == "🤖 DSH 执行结果"


def test_send_card_falls_back_to_text_without_markdown(monkeypatch):
    import feishu_codex_bridge.feishu as feishu_mod

    send_text_calls = []
    monkeypatch.setattr(feishu_mod, "send_text",
                        lambda client, chat_id, text: send_text_calls.append((chat_id, text)))
    send_card(object(), "oc_1", "简单的一句话")
    assert send_text_calls == [("oc_1", "简单的一句话")]


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


def test_handle_commands_clear_acknowledges_shared_session_mode():
    from feishu_codex_bridge.backend import get_backend
    from feishu_codex_bridge.bridge import handle_commands

    backend = get_backend({})
    reply = handle_commands("新会话", backend)
    assert "全新上下文" in reply
    reply = handle_commands("退出会话", backend)
    assert "全新上下文" in reply
    assert handle_commands("查看会话", backend) is None
    assert handle_commands("查询系统状态", backend) is None


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


def test_shared_session_roundtrip(tmp_path, monkeypatch):
    """共用会话 id：同一后端稳定复用；「新会话」轮换 id（不删文件）。"""
    import feishu_codex_bridge.bridge as bridge_mod
    from feishu_codex_bridge.backend import get_backend

    monkeypatch.setattr(bridge_mod, "_SESSION_MAP_FILE", tmp_path / ".feishu_sessions.json")
    claude = get_backend({"backend": "claude"})

    sid1 = bridge_mod._shared_session_id(claude)
    sid2 = bridge_mod._shared_session_id(claude)
    assert sid1 == sid2  # 共用会话稳定复用
    assert claude.looks_like(sid1)  # claude 会话 id 是合法 UUID

    bridge_mod._reset_shared_session(claude)
    sid3 = bridge_mod._shared_session_id(claude)
    assert sid3 != sid1  # 重置后轮换到全新 id

    # 重置只动映射、不删会话文件；映射为新版分区格式
    data = json.loads((tmp_path / ".feishu_sessions.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {"sessions"}
    assert set(data["sessions"].keys()) == {"claude"}


def test_shared_session_idle_rotation(tmp_path, monkeypatch):
    """空闲超过阈值自动轮换新会话（会话结束）；未超时沿用同一会话。"""
    import feishu_codex_bridge.bridge as bridge_mod
    from feishu_codex_bridge.backend import get_backend

    map_file = tmp_path / ".feishu_sessions.json"
    monkeypatch.setattr(bridge_mod, "_SESSION_MAP_FILE", map_file)
    backend = get_backend({"backend": "claude"})

    sid1 = bridge_mod._shared_session_id(backend, idle_timeout_sec=600)
    sid2 = bridge_mod._shared_session_id(backend, idle_timeout_sec=600)  # 马上又来一条
    assert sid2 == sid1  # 未超时沿用

    # 把 active_at 拨到 10 分钟前 → 应轮换新会话
    data = json.loads(map_file.read_text(encoding="utf-8"))
    data["sessions"]["claude"]["active_at"] = time.time() - 601
    map_file.write_text(json.dumps(data), encoding="utf-8")
    sid3 = bridge_mod._shared_session_id(backend, idle_timeout_sec=600)
    assert sid3 != sid1  # 空闲超时自动开启新会话

    # 不传 idle_timeout 永不因空闲轮换
    data = json.loads(map_file.read_text(encoding="utf-8"))
    data["sessions"]["claude"]["active_at"] = time.time() - 999999
    map_file.write_text(json.dumps(data), encoding="utf-8")
    sid4 = bridge_mod._shared_session_id(backend)
    assert sid4 == sid3


def test_session_map_migrates_legacy_flat_format(tmp_path, monkeypatch):
    """旧 flat 格式自动迁移：旧 dsh 会话指针保留到 dsh 分区，不丢上下文。"""
    import feishu_codex_bridge.bridge as bridge_mod
    from feishu_codex_bridge.backend import get_backend

    map_file = tmp_path / ".feishu_sessions.json"
    map_file.write_text(
        json.dumps({"shared": "session-feishu-abc12345", "shared_active_at": 123.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge_mod, "_SESSION_MAP_FILE", map_file)

    claude = get_backend({"backend": "claude"})
    sid = bridge_mod._shared_session_id(claude)
    assert claude.looks_like(sid)  # claude 分区新建 UUID

    data = json.loads(map_file.read_text(encoding="utf-8"))
    assert data["sessions"]["dsh"]["id"] == "session-feishu-abc12345"
    assert data["sessions"]["claude"]["id"] == sid


def test_session_map_backends_isolated(tmp_path, monkeypatch):
    """两个后端各存各的会话 id；重置/轮换互不影响。"""
    import feishu_codex_bridge.bridge as bridge_mod
    from feishu_codex_bridge.backend import get_backend

    monkeypatch.setattr(bridge_mod, "_SESSION_MAP_FILE", tmp_path / ".feishu_sessions.json")
    claude = get_backend({"backend": "claude"})
    dsh = get_backend({"backend": "dsh"})

    c1 = bridge_mod._shared_session_id(claude)
    d1 = bridge_mod._shared_session_id(dsh)
    assert d1.startswith("session-feishu-")
    assert c1 != d1

    bridge_mod._reset_shared_session(claude)
    assert bridge_mod._shared_session_id(dsh) == d1  # dsh 分区不受 claude 重置影响
    assert bridge_mod._shared_session_id(claude) != c1


def test_handle_message_dispatches_to_backend(monkeypatch):
    """handle_message 按 backend 分派 run_task，并用动态标题发卡片。"""
    import feishu_codex_bridge.bridge as bridge_mod
    from feishu_codex_bridge.bridge import handle_message

    calls = {}

    def fake_run_task(backend, config, prompt, **kwargs):
        calls.update(backend=backend, config=config, prompt=prompt, **kwargs)
        return "结果文本"

    sent_texts = []
    cards = []
    monkeypatch.setattr(bridge_mod, "run_task", fake_run_task)
    monkeypatch.setattr(
        bridge_mod, "send_text", lambda client, chat_id, text: sent_texts.append(text)
    )
    monkeypatch.setattr(
        bridge_mod,
        "send_card",
        lambda client, chat_id, text, title=None: cards.append((text, title)),
    )

    config = {
        "backend": "claude",
        "workdir": "/tmp/x",
        "allowed_open_ids": ["ou_test"],
        "task_timeout_sec": 42,
        "progress_interval_sec": 3,
    }
    handle_message(make_event("你好"), config, object())

    assert calls["backend"].name == "claude"
    assert calls["prompt"] == "你好"
    assert calls["workdir"] == "/tmp/x"
    assert calls["timeout"] == 42
    assert calls["progress_interval"] == 3
    assert calls["session_id"]  # 固定会话 id 已生成
    assert any("Claude 开始处理" in t for t in sent_texts)
    assert cards and cards[0][0] == "结果文本"
    assert cards[0][1] == "🤖 Claude 执行结果"


def test_single_instance_guard(tmp_path, monkeypatch):
    """flock 单实例保护：占用锁后第二次获取失败（拒绝重复启动）。"""
    import feishu_codex_bridge.bridge as bridge_mod

    pid_file = tmp_path / ".bridge.pid"
    monkeypatch.setattr(bridge_mod, "_PID_FILE", pid_file)
    bridge_mod._PID_LOCK_FH = None

    # 第一次获取成功，锁随进程持有
    bridge_mod._acquire_single_instance()
    assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    # 锁仍被持有 → 第二次拒绝
    with pytest.raises(SystemExit):
        bridge_mod._acquire_single_instance()
