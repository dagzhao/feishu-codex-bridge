#!/bin/bash
# 切换飞书桥接后端：claude <-> dsh
#
#   ./switch_backend.sh dsh        # 切到 DSH（DeepSeek Harness）
#   ./switch_backend.sh claude     # 切回 Claude Code
#   ./switch_backend.sh status     # 只看当前状态
#
# 改完 config.json 后【延迟 25 秒】重启桥接 —— 这样调用方（Claude/dsh/人）
# 有时间把「已切换」的回复发出去，不会被重启掐断。
#
# 注意：下面每个变量都写成 ${var}，因为紧跟中文字符时 bash 会把多字节字符
# 当成变量名的一部分（踩过：line 52: target: unbound variable）。
set -euo pipefail
cd "$(dirname "$0")"

LAUNCHD_LABEL="gui/501/com.dsh.feishu-bridge"
DSH_BIN="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
DELAY="${SWITCH_DELAY:-25}"

bridge_pid() {
  ps -eo pid,command | grep "[f]eishu_codex_bridge" | awk '{print $1}' | head -1
}

case "${1:-status}" in
  status)
    echo "当前 backend: $(python3 -c "import json;print(json.load(open('config.json')).get('backend','claude'))")"
    echo "dsh_bin: $(python3 -c "import json;print(json.load(open('config.json')).get('dsh_bin') or '(空/自动解析)')")"
    echo "桥接进程 PID: $(bridge_pid || true)"
    exit 0
    ;;
  claude|dsh) target="$1" ;;
  *) echo "用法: $0 claude|dsh|status" >&2; exit 1 ;;
esac

if [ "$target" = dsh ] && [ ! -x "$DSH_BIN" ]; then
  echo "❌ DSH 可执行文件不存在或不可执行: ${DSH_BIN}" >&2
  echo "   （DeepSeek Harness.app 是否已安装/更新？路径可能变了）" >&2
  exit 1
fi

cp -p config.json "config.json.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$target" "$DSH_BIN" <<'PY'
import json, sys
target, dsh_bin = sys.argv[1], sys.argv[2]
p = 'config.json'
d = json.load(open(p))
d['backend'] = target
if target == 'dsh':
    d['dsh_bin'] = dsh_bin
with open(p, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
    f.write('\n')
PY

echo "✅ config.json 已切到 backend=${target}（已备份）"
echo "⏳ ${DELAY}s 后重启桥接（launchctl kickstart -k ${LAUNCHD_LABEL}）"

nohup bash -c "sleep ${DELAY}; launchctl kickstart -k ${LAUNCHD_LABEL}; sleep 6; echo \"\$(date '+%F %T') 切到 ${target} 后进程: \$(ps -eo pid,command | grep '[f]eishu_codex_bridge' | awk '{print \$1}' | tr '\n' ' ')\" >> /tmp/bridge_switch.log" \
  >>/tmp/bridge_switch.log 2>&1 &
