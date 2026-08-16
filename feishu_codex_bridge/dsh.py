"""调用本机 DSH（DeepSeek Harness）headless 模式执行任务，把结果回传飞书。

DSH headless 是一次性任务模式：`dsh --profile headless <任务>` 创建单个
Agent 执行任务、打印最终答复并退出。无 Codex 那样的 JSON 事件流，因此
不再有逐步骤的实时过程提示——收到指令后的同步确认就是进度反馈。
"""

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Optional

LOGGER = logging.getLogger("feishu_codex_bridge.dsh")


def run_dsh(
    prompt: str,
    dsh_bin: str,
    workdir: str,
    timeout: int,
    on_progress: Optional[Callable[[str], None]] = None,
    progress_interval: float = 10.0,
) -> str:
    """运行 dsh --profile headless 并返回最终答复文本。

    on_progress 保留兼容（DSH headless 无事件流，通常只在开始时收到一次
    「正在处理」提示）。
    """
    cmd = [dsh_bin, "--profile", "headless", prompt]
    LOGGER.info("调用 DSH headless: %s", prompt[:200])

    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    stderr_lines: list = []
    threading.Thread(
        target=lambda: stderr_lines.extend(proc.stderr), daemon=True
    ).start()

    state = {"timed_out": False, "progressed": False}

    def _progress(text: str) -> None:
        now = time.monotonic()
        if on_progress and (not state["progressed"] or now - state["_last"] >= progress_interval):
            state["progressed"] = True
            state["_last"] = now
            try:
                on_progress(text)
            except Exception:
                LOGGER.exception("发送过程提示失败")

    def _kill() -> None:
        if proc.poll() is None:
            state["timed_out"] = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()

    out_lines: list = []
    try:
        _progress("🧠 DSH 正在处理…")
        for raw in proc.stdout:
            line = raw.strip()
            if line:
                out_lines.append(line)
        proc.wait()
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
        raise
    finally:
        timer.cancel()

    if state["timed_out"]:
        return f"⚠️ DSH 任务超过 {timeout} 秒未完成，已终止。可以换更简单的指令重试。"

    text = "\n".join(out_lines).strip()
    if text:
        return text
    err = "".join(stderr_lines).strip()
    if proc.returncode != 0:
        return f"❌ DSH 执行失败（退出码 {proc.returncode}）：\n{err[-1500:]}"
    return err or "✅ 处理完成。"
