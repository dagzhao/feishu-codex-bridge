import json
import time

from feishu_codex_bridge.cleanup import (
    DSH_SESSIONS_ROOT,
    cleanup_one_shot_sessions,
    is_one_shot_session,
)


def _make_session(tmp_path, name: str, header: dict, mtime: float, zstd: bool = False):
    """建一个会话目录（位于项目子目录下），返回 artifact 路径。"""
    proj_dir = tmp_path / "--Users-test--"
    sess_dir = proj_dir / name  # monkeypatched 根下的项目目录
    sess_dir.mkdir(parents=True, exist_ok=True)
    artifact = sess_dir / ("session.jsonl.zstd" if zstd else "session.jsonl")
    if zstd:
        import shutil
        import subprocess

        raw = (json.dumps(header) + "\n").encode()
        proc = subprocess.run(
            ["zstd", "-q", "-c"], input=raw, capture_output=True, check=True
        )
        artifact.write_bytes(proc.stdout)
    else:
        artifact.write_text(json.dumps(header) + "\n", encoding="utf-8")
    import os

    os.utime(artifact, (mtime, mtime))
    return artifact


def test_is_one_shot_session_plain(tmp_path, monkeypatch):
    monkeypatch.setattr("feishu_codex_bridge.cleanup.DSH_SESSIONS_ROOT", tmp_path)
    one_shot = _make_session(
        tmp_path, "s1", {"type": "session", "id": "s1", "cwd": "/tmp"}, time.time()
    )
    assert is_one_shot_session(one_shot) is True

    main = _make_session(
        tmp_path,
        "s2",
        {"type": "session", "id": "s2", "cwd": "/tmp", "agentPreset": "standard"},
        time.time(),
    )
    assert is_one_shot_session(main) is False


def test_cleanup_removes_only_old_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr("feishu_codex_bridge.cleanup.DSH_SESSIONS_ROOT", tmp_path)
    now = time.time()
    # 旧的一次性会话 → 删
    _make_session(tmp_path, "session-old-shot", {"type": "session", "id": "a"}, now - 48 * 3600)
    # 新的一次性会话 → 留
    _make_session(tmp_path, "session-new-shot", {"type": "session", "id": "b"}, now - 3600)
    # 旧的主会话（agentPreset）→ 留
    _make_session(
        tmp_path,
        "session-old-main",
        {"type": "session", "id": "c", "agentPreset": "standard"},
        now - 48 * 3600,
    )

    removed = cleanup_one_shot_sessions(max_age_hours=24)
    proj = tmp_path / "--Users-test--"
    assert removed == 1
    assert not (proj / "session-old-shot").exists()
    assert (proj / "session-new-shot").exists()
    assert (proj / "session-old-main").exists()
