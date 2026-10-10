import stat

import pytest

from feishu_codex_bridge import claude as claude_mod
from feishu_codex_bridge.claude import run_claude, session_exists

OK_SID = "11111111-1111-1111-1111-111111111111"


def _fake_claude(tmp_path, script: str) -> str:
    path = tmp_path / "fake-claude"
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _read_calls(tmp_path) -> list:
    log = tmp_path / "calls.log"
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fake_projects_with_session(tmp_path, monkeypatch, sid):
    proj = tmp_path / "projects"
    proj.mkdir()
    (proj / f"{sid}.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(claude_mod, "_claude_projects_dir", lambda workdir: proj)


def test_create_when_session_absent(tmp_path):
    """会话文件不存在 → 直接 --session-id 创建（快路径，不盲试 resume）。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
echo '{"result":"你好呀","is_error":false}'
"""
    result = run_claude(
        "测试任务", _fake_claude(tmp_path, script), str(tmp_path), timeout=30, session_id=OK_SID
    )
    assert result == "你好呀"
    calls = _read_calls(tmp_path)
    assert len(calls) == 1
    tokens = calls[0].split()
    assert tokens[0] == "-p"
    assert "--" in tokens  # prompt 在 -- 之后（- 开头的指令不会被当选项）
    assert tokens[tokens.index("--") + 1] == "测试任务"
    assert "--session-id" in tokens and OK_SID in tokens
    assert "--resume" not in tokens
    assert "--output-format" in tokens and "json" in tokens
    assert "--dangerously-skip-permissions" in tokens


def test_resume_when_session_file_exists(tmp_path, monkeypatch):
    """会话文件存在 → 走 --resume，且只调用一次。"""
    _fake_projects_with_session(tmp_path, monkeypatch, OK_SID)
    script = """#!/bin/sh
echo "$@" >> calls.log
echo '{"result":"续聊回复","is_error":false}'
"""
    result = run_claude(
        "继续", _fake_claude(tmp_path, script), str(tmp_path), timeout=30, session_id=OK_SID
    )
    assert result == "续聊回复"
    calls = _read_calls(tmp_path)
    assert len(calls) == 1
    assert "--resume" in calls[0].split()


def test_resume_falls_back_to_create(tmp_path, monkeypatch):
    """resume 报 No conversation found → 自动改用 --session-id 重建成功。"""
    _fake_projects_with_session(tmp_path, monkeypatch, OK_SID)
    script = """#!/bin/sh
echo "$@" >> calls.log
case "$*" in
  *--resume*)
    echo "No conversation found with session ID: xxx" >&2
    exit 1
    ;;
esac
echo '{"result":"重建成功","is_error":false}'
"""
    result = run_claude(
        "你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30, session_id=OK_SID
    )
    assert result == "重建成功"
    calls = _read_calls(tmp_path)
    assert len(calls) == 2
    assert "--resume" in calls[0].split()
    assert "--session-id" in calls[1].split()


def test_create_falls_back_to_resume_on_already_in_use(tmp_path):
    """create 撞 already in use（快路径判断失误）→ 自动改用 --resume。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
case "$*" in
  *--session-id*)
    echo "Session ID is already in use." >&2
    exit 1
    ;;
esac
echo '{"result":"resume 成功","is_error":false}'
"""
    result = run_claude(
        "你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30, session_id=OK_SID
    )
    assert result == "resume 成功"
    calls = _read_calls(tmp_path)
    assert len(calls) == 2
    assert "--session-id" in calls[0].split()
    assert "--resume" in calls[1].split()


def test_no_fallback_on_unrelated_error(tmp_path):
    """不匹配会话状态错误的失败不回退，只调用一次。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
echo 'boom' >&2
exit 3
"""
    result = run_claude(
        "你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30, session_id=OK_SID
    )
    assert "退出码 3" in result and "boom" in result
    assert len(_read_calls(tmp_path)) == 1


def test_timeout_does_not_fallback(tmp_path):
    """超时不回退（会话可能仍在工作中），且只调用一次。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
sleep 30
"""
    result = run_claude(
        "你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=1, session_id=OK_SID
    )
    assert "超过 1 秒未完成" in result
    assert len(_read_calls(tmp_path)) == 1


def test_is_error_json_returns_error_text(tmp_path):
    script = """#!/bin/sh
echo "$@" >> calls.log
echo '{"result":"达到最大轮数","is_error":true,"subtype":"error_max_turns"}'
"""
    result = run_claude("你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30)
    assert "❌" in result and "error_max_turns" in result and "达到最大轮数" in result
    assert len(_read_calls(tmp_path)) == 1


def test_non_json_stdout_passthrough(tmp_path):
    script = """#!/bin/sh
printf '这是纯文本输出\\n'
"""
    result = run_claude("你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30)
    assert result == "这是纯文本输出"


def test_nonzero_rc_reports_stderr(tmp_path):
    script = """#!/bin/sh
echo 'oops' >&2
exit 2
"""
    result = run_claude("你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30)
    assert "退出码 2" in result and "oops" in result


def test_permission_mode_flag_switch(tmp_path):
    """非 bypass 权限模式 → --permission-mode <值> 且不带 skip 标志。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
echo '{"result":"ok","is_error":false}'
"""
    result = run_claude(
        "你好",
        _fake_claude(tmp_path, script),
        str(tmp_path),
        timeout=30,
        permission_mode="plan",
    )
    assert result == "ok"
    tokens = _read_calls(tmp_path)[0].split()
    assert "--permission-mode" in tokens and "plan" in tokens
    assert "--dangerously-skip-permissions" not in tokens


def test_model_flag_passed(tmp_path):
    """model 非空时命令行带 --model；留空则不传（用当前默认模型）。"""
    script = """#!/bin/sh
echo "$@" >> calls.log
echo '{"result":"ok","is_error":false}'
"""
    bin_path = _fake_claude(tmp_path, script)
    assert (
        run_claude("你好", bin_path, str(tmp_path), timeout=30, model="sonnet") == "ok"
    )
    tokens = _read_calls(tmp_path)[0].split()
    assert "--model" in tokens and "sonnet" in tokens

    (tmp_path / "calls.log").unlink()
    run_claude("你好", bin_path, str(tmp_path), timeout=30)
    assert "--model" not in _read_calls(tmp_path)[0].split()


def test_extra_env_injected(tmp_path):
    """extra_env 注入子进程环境（用于 ANTHROPIC_AUTH_TOKEN）。"""
    script = """#!/bin/sh
printf '{"result":"%s","is_error":false}\\n' "$ANTHROPIC_AUTH_TOKEN"
"""
    result = run_claude(
        "你好",
        _fake_claude(tmp_path, script),
        str(tmp_path),
        timeout=30,
        extra_env={"ANTHROPIC_AUTH_TOKEN": "sk-test-123"},
    )
    assert result == "sk-test-123"


def test_stdin_is_devnull(tmp_path):
    """stdin 是 /dev/null：脚本 read 立即 EOF，不会读取到杂散输入。"""
    script = """#!/bin/sh
if read -r line; then
  printf '{"result":"got:%s","is_error":false}\\n' "$line"
else
  echo '{"result":"eof","is_error":false}'
fi
"""
    result = run_claude("你好", _fake_claude(tmp_path, script), str(tmp_path), timeout=30)
    assert result == "eof"


def test_claude_projects_dir_escapes_workdir():
    """工作目录转义规则：每个非字母数字字符 → 一个 '-'（不折叠）。"""
    assert (
        claude_mod._claude_projects_dir("/Users/x/Documents/feishu-workspace").name
        == "-Users-x-Documents-feishu-workspace"
    )


def test_session_exists_checks_jsonl_file(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    assert session_exists("abc", "/any/workdir", projects_dir=root) is False
    (root / "abc.jsonl").write_text("{}", encoding="utf-8")
    assert session_exists("abc", "/any/workdir", projects_dir=root) is True


def test_missing_binary_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_claude(
            "你好", str(tmp_path / "no-such-claude"), str(tmp_path), timeout=5, session_id=None
        )
