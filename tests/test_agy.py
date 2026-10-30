"""tests for Antigravity (agy) backend and CLI integration."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from feishu_codex_bridge import agy
from feishu_codex_bridge.backend import BACKENDS, get_backend, run_task
from feishu_codex_bridge.config import get_config_path, get_instance_name, resolve_agy_bin


def test_agy_backend_registered():
    assert "agy" in BACKENDS
    backend = BACKENDS["agy"]
    assert backend.name == "agy"
    assert backend.label == "Antigravity"
    assert backend.bin_key == "agy_bin"
    assert backend.looks_like("c1065aa3-fc90-485d-a9c5-6de0990fefa9")
    assert not backend.looks_like("session-feishu-12345678")


def test_build_cmd():
    cmd = agy._build_cmd("agy", "hello", session_id="abc-123", model="gemini-3.8-flash", effort="high")
    assert cmd == [
        "agy",
        "--output-format",
        "json",
        "--conversation",
        "abc-123",
        "--model",
        "gemini-3.8-flash",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
        "-p",
        "hello",
    ]


def test_parse_result_success():
    raw = json.dumps({
        "conversation_id": "test-uuid",
        "status": "SUCCESS",
        "response": "Here is the response",
        "duration_seconds": 1.23,
    })
    res = agy._parse_result(raw)
    assert res is not None
    assert res["conversation_id"] == "test-uuid"
    assert res["response"] == "Here is the response"


def test_parse_result_with_warnings():
    raw = "warning: conversation abc not found\n" + json.dumps({
        "conversation_id": "new-uuid",
        "status": "SUCCESS",
        "response": "Responded",
    })
    res = agy._parse_result(raw)
    assert res is not None
    assert res["conversation_id"] == "new-uuid"


def test_format_result():
    res = agy._format_result({"status": "SUCCESS", "response": "Done!"})
    assert res == "Done!"

    err = agy._format_result({"status": "ERROR", "response": "Something broke"})
    assert "❌ Antigravity 执行失败" in err


@patch.object(agy, "_run_once")
def test_run_agy_updates_session_id(mock_run):
    mock_run.return_value = (
        0,
        json.dumps({
            "conversation_id": "cid-999",
            "status": "SUCCESS",
            "response": "Hello world",
        }),
        "",
        False,
    )
    saved_cid = []
    text = agy.run_agy(
        "hi",
        "agy",
        "/tmp",
        300,
        on_session_id=lambda cid: saved_cid.append(cid),
    )
    assert text == "Hello world"
    assert saved_cid == ["cid-999"]


def test_instance_helpers():
    cfg_path = get_config_path(["--config", "config.agy.json"])
    assert cfg_path.name == "config.agy.json"

    instance = get_instance_name({}, cfg_path)
    assert instance == "agy"

    instance_explicit = get_instance_name({"instance": "custom"}, cfg_path)
    assert instance_explicit == "custom"
