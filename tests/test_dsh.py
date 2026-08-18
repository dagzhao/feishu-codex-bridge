import stat

from feishu_codex_bridge.dsh import run_dsh


def _fake_dsh(tmp_path, script: str) -> str:
    path = tmp_path / "fake-dsh"
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_run_dsh_returns_stdout_text(tmp_path):
    script = """#!/bin/sh
echo '这是 DSH 的最终答复'
"""
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=30,
    )
    assert result == "这是 DSH 的最终答复"


def test_run_dsh_passes_permission_mode_env(tmp_path):
    script = """#!/bin/sh
echo "mode=$DSH_PERMISSION_MODE"
"""
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=30,
        permission_mode="danger-full-access",
    )
    assert result == "mode=danger-full-access"


def test_run_dsh_unset_permission_mode_without_arg(tmp_path):
    script = """#!/bin/sh
echo "mode=${DSH_PERMISSION_MODE:-unset}"
"""
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=30,
    )
    assert result == "mode=unset"


def test_run_dsh_sends_start_progress(tmp_path):
    script = """#!/bin/sh
sleep 0.2
echo '完成'
"""
    progress = []
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=30,
        on_progress=progress.append,
        progress_interval=0,
    )
    assert result == "完成"
    assert any("DSH 正在处理" in text for text in progress)


def test_run_dsh_timeout_kills_process(tmp_path):
    script = """#!/bin/sh
sleep 30
"""
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=1,
    )
    assert "超过 1 秒未完成" in result


def test_run_dsh_returns_error_without_output(tmp_path):
    script = """#!/bin/sh
echo 'oops' >&2
exit 2
"""
    result = run_dsh(
        "测试任务",
        _fake_dsh(tmp_path, script),
        str(tmp_path),
        timeout=30,
    )
    assert "退出码 2" in result and "oops" in result
