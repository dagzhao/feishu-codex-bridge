import sqlite3

from feishu_codex_bridge.sessions import list_recent_sessions


def _make_db(tmp_path):
    db = tmp_path / "state_5.sqlite"
    rollout_keep = tmp_path / "rollout-keep.jsonl"
    rollout_keep.write_text("", encoding="utf-8")
    rollout_exec = tmp_path / "rollout-exec.jsonl"
    rollout_exec.write_text("", encoding="utf-8")
    rollout_guardian = tmp_path / "rollout-guardian.jsonl"
    rollout_guardian.write_text("", encoding="utf-8")
    rollout_archived = tmp_path / "rollout-archived.jsonl"
    rollout_archived.write_text("", encoding="utf-8")
    rollout_empty_title = tmp_path / "rollout-empty-title.jsonl"
    rollout_empty_title.write_text("", encoding="utf-8")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE threads ("
        "id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER, updated_at INTEGER, "
        "source TEXT, title TEXT, first_user_message TEXT, archived INTEGER)"
    )
    rows = [
        # 正常桌面会话
        ("019f-aaa", str(rollout_keep), 1000, 5000, "vscode", "继续写代码", "继续写代码", 0),
        # exec 一次性会话，应被过滤
        ("019f-bbb", str(rollout_exec), 1000, 4000, "exec", "您好", "您好", 0),
        # 子代理/审批会话（source 为 JSON），应被过滤
        ("019f-ccc", str(rollout_guardian), 1000, 3000, '{"subagent":{"other":"guardian"}}', "The following is the Codex agent history", "", 0),
        # 已归档，应被过滤
        ("019f-ddd", str(rollout_archived), 1000, 2000, "vscode", "旧会话", "旧会话", 1),
        # rollout 文件不存在，应被过滤
        ("019f-eee", "/tmp/not-exists.jsonl", 1000, 1500, "vscode", "已删除", "已删除", 0),
        # 标题为空时回退到首条消息
        ("019f-fff", str(rollout_empty_title), 1000, 1000, "vscode", "", "从首条消息取标题", 0),
    ]
    conn.executemany(
        "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return db


def test_list_recent_sessions_from_threads(monkeypatch, tmp_path):
    db = _make_db(tmp_path)
    index = tmp_path / "session_index.jsonl"
    index.write_text("", encoding="utf-8")
    monkeypatch.setattr("feishu_codex_bridge.sessions.index_path", lambda: index)

    result = list_recent_sessions()
    titles = [item["title"] for item in result]
    assert titles == ["继续写代码", "从首条消息取标题"]


def test_list_recent_sessions_limit(monkeypatch, tmp_path):
    db = _make_db(tmp_path)
    index = tmp_path / "session_index.jsonl"
    index.write_text("", encoding="utf-8")
    monkeypatch.setattr("feishu_codex_bridge.sessions.index_path", lambda: index)

    result = list_recent_sessions(limit=1)
    assert [item["title"] for item in result] == ["继续写代码"]
