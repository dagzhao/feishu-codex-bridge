#!/usr/bin/env python3
"""更换桥接服务的 ANTHROPIC_AUTH_TOKEN（token / key）并重启服务。

claude 的认证 token 存在 ``config.json`` 的 ``claude_env.ANTHROPIC_AUTH_TOKEN``
（由 launchd 环境下的桥接进程注入给 claude 子进程；launchd 拿不到
``~/.zshrc`` 里的 export）。

用法::

    cd ~/Documents/harnessWorkSpace/feishu-bridge
    .venv/bin/python update_token.py             # 从 ~/.zshrc 读取并同步（推荐：先更新 zshrc）
    .venv/bin/python update_token.py sk-新token  # 直接指定（注意会留在 shell 历史里）

做三件事：
  1. 写入 config.json（不打印 token 内容，只报长度与是否变化）
  2. chmod 600 config.json
  3. launchctl kickstart -k gui/<uid>/com.dsh.feishu-bridge 重启服务

验证：重启后 ``tail -3 bridge.log`` 应看到「飞书桥接服务启动」；然后在
飞书发一条消息确认能正常回复。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
LABEL = "com.dsh.feishu-bridge"


def read_token_from_zshrc() -> str:
    """从 ~/.zshrc 提取 export ANTHROPIC_AUTH_TOKEN 的值（没有则空串）。"""
    zshrc = Path.home() / ".zshrc"
    try:
        content = zshrc.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("export ANTHROPIC_AUTH_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    token = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if token:
        source = "命令行参数"
    else:
        token = read_token_from_zshrc()
        source = "~/.zshrc"
    if not token:
        print(
            "✗ 未找到 token：先在 ~/.zshrc 里 export ANTHROPIC_AUTH_TOKEN=...，"
            "或把新 token 作为参数传入。",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ 读取 config.json 失败：{exc}", file=sys.stderr)
        return 1

    env = cfg.get("claude_env")
    if not isinstance(env, dict):
        env = {}
    old = env.get("ANTHROPIC_AUTH_TOKEN", "")
    env["ANTHROPIC_AUTH_TOKEN"] = token
    cfg["claude_env"] = env
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(CONFIG, 0o600)
    changed = "（与旧值相同）" if old == token else "（已更新）"
    print(f"✓ config.json 已写入 token（来源：{source}，长度 {len(token)}）{changed}")

    if sys.platform != "darwin":
        print("非 macOS 环境：请手动重启桥接服务使新 token 生效。")
        return 0

    target = f"gui/{os.getuid()}/{LABEL}"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", target], capture_output=True, text=True
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        print(f"✗ 重启服务失败：{detail}", file=sys.stderr)
        print(f"  可手动重启：launchctl kickstart -k {target}", file=sys.stderr)
        return 1
    print(f"✓ 已重启服务（{target}）")
    print("验证：tail -3 bridge.log 应出现「飞书桥接服务启动」；再到飞书发消息确认。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
