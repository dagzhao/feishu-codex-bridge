"""调用本机 Codex 执行任务。"""

import logging
import subprocess

LOGGER = logging.getLogger("feishu_codex_bridge.codex")


def run_codex(prompt: str, codex_bin: str, workdir: str, timeout: int) -> str:
    cmd = [
        codex_bin, "exec", "--skip-git-repo-check",
        "-c", "sandbox_mode=danger-full-access",
        "-c", "approval_policy=never",
        prompt,
    ]
    LOGGER.info("调用 codex: %s", prompt[:200])
    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"⚠️ Codex 任务超过 {timeout} 秒未完成，已终止。可以换更简单的指令重试。"
    output = (result.stdout or "").strip()
    if not output:
        output = (result.stderr or "").strip()
    if result.returncode != 0:
        return f"❌ Codex 执行失败（退出码 {result.returncode}）：\n{output[-1500:]}"
    return output
