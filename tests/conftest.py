"""全局测试夹具：确保测试永不触碰线上桥接状态。

``feishu_codex_bridge.bridge`` 的三个状态文件默认就落在项目目录下，也就是
飞书桥真实使用的文件：

- ``.feishu_sessions.json``：固定会话映射（决定飞书消息续聊哪个会话）
- ``.bridge_state.json``：最近聊天状态
- ``.bridge.pid``：单实例 flock 锁文件

任何直接调用 ``handle_commands("新会话")`` / ``_shared_session_id()`` /
``_acquire_single_instance()`` 的用例，如果不做隔离就会读写这些线上文件。
真实事故：``tests/test_bridge.py`` 的命令处理用例直接调用
``handle_commands("新会话")``，跑一次 pytest 就清掉了固定会话映射，导致下一条
飞书消息开新会话、续聊上下文被静默重置。

这里用 autouse fixture 把三者统一重定向到 ``tmp_path``；用例内部再
monkeypatch 具体路径（如 ``_SESSION_MAP_FILE``）同样有效，且只在该用例内生效。
"""

import pytest

from feishu_codex_bridge import bridge as bridge_mod


@pytest.fixture(autouse=True)
def isolate_bridge_state(tmp_path, monkeypatch):
    """把桥接状态文件重定向到临时目录，避免污染线上状态。"""
    monkeypatch.setattr(
        bridge_mod, "_SESSION_MAP_FILE", tmp_path / ".feishu_sessions.json"
    )
    monkeypatch.setattr(bridge_mod, "_STATE_FILE", tmp_path / ".bridge_state.json")
    monkeypatch.setattr(bridge_mod, "_PID_FILE", tmp_path / ".bridge.pid")
