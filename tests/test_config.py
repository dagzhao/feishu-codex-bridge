import json
import stat

from feishu_codex_bridge import config as config_mod
from feishu_codex_bridge.config import load_config, resolve_claude_bin


def _make_executable(path):
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _write_config(tmp_path, **overrides):
    cfg = {"app_id": "cli_xxxxxxxxxxxxxxxxxxxx", "app_secret": "s"}
    cfg.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_resolve_claude_bin_prefers_valid_configured(tmp_path):
    fake = _make_executable(tmp_path / "my-claude")
    assert resolve_claude_bin(str(fake)) == str(fake)


def test_resolve_claude_bin_falls_back_when_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(config_mod, "_CLAUDE_FALLBACK_BIN", tmp_path / "missing-claude")
    assert resolve_claude_bin("/no/such/claude") == "/no/such/claude"


def test_resolve_claude_bin_uses_local_bin_fallback(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "claude")
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(config_mod, "_CLAUDE_FALLBACK_BIN", fake)
    assert resolve_claude_bin("") == str(fake)


def test_load_config_defaults_backend_claude(tmp_path):
    cfg = load_config(_write_config(tmp_path))
    assert cfg["backend"] == "claude"
    assert isinstance(cfg["claude_env"], dict)
    assert cfg["claude_bin"]  # 已解析出可用路径（本机存在 claude）
    assert cfg["dsh_bin"]  # dsh 解析保持兼容


def test_load_config_unknown_backend_falls_back(tmp_path):
    cfg = load_config(_write_config(tmp_path, backend="wut"))
    assert cfg["backend"] == "claude"


def test_load_config_normalizes_claude_env(tmp_path):
    cfg = load_config(_write_config(tmp_path, claude_env="not-a-dict"))
    assert cfg["claude_env"] == {}
