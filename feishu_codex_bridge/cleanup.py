"""定期清理 dsh headless 一次性任务会话，保持会话列表干净。

会话存储：`~/.dsh/sessions/<项目目录键>/session-*/session.jsonl[.zstd]`
- **主会话 / 桌面会话**（web GUI 等）：session header 带 `agentPreset` → 永不删除
- **headless 一次性任务会话**（飞书桥接每次调用产生）：header 无 `agentPreset`
  → 超过 `max_age_hours` 即删除

删除依据是 header 与文件 mtime，双重保护主会话不被误删。
"""

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger("feishu_codex_bridge.cleanup")

DSH_SESSIONS_ROOT = Path.home() / ".dsh" / "sessions"
_HEADER_SNIFF_BYTES = 2048


def _read_header_bytes(artifact: Path) -> Optional[bytes]:
    """读取会话文件头部原始字节（zstd 文件先解压前段）。"""
    try:
        if artifact.name.endswith(".zstd"):
            proc = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(artifact)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            try:
                data = proc.stdout.read(_HEADER_SNIFF_BYTES) if proc.stdout else b""
                proc.stdout.close()
            finally:
                proc.kill()
                proc.wait()
            return data or None
        with artifact.open("rb") as fh:
            return fh.read(_HEADER_SNIFF_BYTES)
    except (OSError, subprocess.SubprocessError):
        return None


def is_one_shot_session(artifact: Path) -> bool:
    """headless 一次性任务会话的 header 没有 agentPreset。"""
    header = _read_header_bytes(artifact)
    if header is None:
        return False  # 读不到就当主会话，保守处理
    try:
        first_line = header.split(b"\n", 1)[0].decode("utf-8", "ignore")
        meta = json.loads(first_line)
    except (ValueError, json.JSONDecodeError):
        return False
    return "agentPreset" not in meta


def cleanup_one_shot_sessions(max_age_hours: float = 24.0) -> int:
    """删除超过 max_age_hours 的一次性任务会话，返回删除数量。"""
    if not DSH_SESSIONS_ROOT.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for project_dir in DSH_SESSIONS_ROOT.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for sess_dir in project_dir.iterdir():
            if not sess_dir.is_dir() or not sess_dir.name.startswith("session-"):
                continue
            artifact = next(
                (
                    sess_dir / n
                    for n in ("session.jsonl.zstd", "session.jsonl")
                    if (sess_dir / n).exists()
                ),
                None,
            )
            if artifact is None:
                continue
            try:
                mtime = artifact.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            if not is_one_shot_session(artifact):
                continue
            shutil.rmtree(sess_dir, ignore_errors=True)
            removed += 1
            LOGGER.info("清理过期一次性会话: %s", sess_dir.name)
    if removed:
        LOGGER.info("一次性会话清理完成，共删除 %d 个", removed)
    return removed
