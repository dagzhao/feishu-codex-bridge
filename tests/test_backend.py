from feishu_codex_bridge.backend import BACKENDS, get_backend, run_task


def test_get_backend_defaults_to_claude():
    assert get_backend({}).name == "claude"
    assert get_backend({"backend": None}).name == "claude"
    assert get_backend({"backend": "claude"}).name == "claude"
    assert get_backend({"backend": "dsh"}).name == "dsh"


def test_get_backend_unknown_falls_back_to_claude(caplog):
    with caplog.at_level("WARNING", logger="feishu_codex_bridge.backend"):
        backend = get_backend({"backend": "wut"})
    assert backend.name == "claude"
    assert any("未知的 backend" in r.message for r in caplog.records)


def test_run_task_uses_backend_specific_keys(monkeypatch):
    """run_task 按各后端的 bin_key/perm_key/env_key 取值传给 run()。"""
    captured = {}

    def fake_run(prompt, bin_path, workdir, timeout, **kwargs):
        captured.update(prompt=prompt, bin_path=bin_path, workdir=workdir, timeout=timeout)
        captured.update(kwargs)
        return "ok"

    for name in ("claude", "dsh"):
        backend = BACKENDS[name]
        monkeypatch.setattr(backend, "run", fake_run)
        config = {
            backend.bin_key: f"/fake/{name}",
            backend.perm_key: "mode-x",
            "claude_env": {"K": "V"},
            "claude_model": "m1",
        }
        result = run_task(backend, config, "hi", workdir="/w", timeout=9, session_id="s1")
        assert result == "ok"
        assert captured["bin_path"] == f"/fake/{name}"
        assert captured["permission_mode"] == "mode-x"
        assert captured["session_id"] == "s1"
        if name == "claude":
            assert captured["extra_env"] == {"K": "V"}
            assert captured["model"] == "m1"
        else:
            assert "extra_env" not in captured
            assert "model" not in captured
        captured.clear()


def test_run_task_missing_binary_returns_friendly_text(tmp_path):
    backend = get_backend({"backend": "claude"})
    config = {"claude_bin": str(tmp_path / "does-not-exist")}
    result = run_task(backend, config, "hi", workdir=str(tmp_path), timeout=5, session_id=None)
    assert "找不到 Claude 可执行文件" in result


def test_run_task_with_on_session_id_does_not_break_dsh_or_claude():
    """验证即使传入 on_session_id，run_task 调用 dsh/claude 真实函数时也不会因签名不符报 TypeError。"""
    from unittest.mock import patch

    for backend_name in ("dsh", "claude"):
        backend = BACKENDS[backend_name]
        with patch.object(backend, "run", return_value="ok") as mock_run:
            result = run_task(
                backend,
                {},
                "test",
                workdir="/tmp",
                timeout=10,
                on_session_id=lambda sid: None,
            )
            assert result == "ok"
            # dsh 和 claude 的 supports_session_callback 均为 False，不应收到 on_session_id
            assert "on_session_id" not in mock_run.call_args.kwargs


def test_backends_supports_session_callback_flag():
    """检查各后端的 supports_session_callback 配置。"""
    assert BACKENDS["agy"].supports_session_callback is True
    assert BACKENDS["dsh"].supports_session_callback is False
    assert BACKENDS["claude"].supports_session_callback is False

