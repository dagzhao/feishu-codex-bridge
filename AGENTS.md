# AGENTS.md

## 项目简介

飞书机器人 → 本机 AI CLI（Claude Code / DSH）的桥接服务：用户在飞书发消息，
服务按 `config.json` 的 `backend` 开关（默认 `claude`）调用 `claude -p`
（Claude Code headless）或 `dsh --profile headless` 执行任务，并把结果发回
飞书。使用飞书官方长连接模式，无需公网服务器。支持**固定会话续聊**：桥接层
为每个后端维护一个固定会话 id（**所有飞书消息共用一个会话**，不区分用户），
存在 `.feishu_sessions.json` 的 `sessions.<backend>` 分区里（真·多轮上下文）；
「新会话」命令轮换当前后端的 id，下次对话走全新上下文；旧会话文件由各自
后端侧管理（桥接不删会话文件）。切换 backend 时两个分区各自保留，切回来
仍能延续该后端的上下文。

- **claude 后端**（默认）：首次消息 `--session-id <uuid>` 创建会话，之后
  `--resume <uuid>` 续聊（会话文件在 `~/.claude/projects/<workdir 转义>/`）；
  双向自动回退：resume 报 `No conversation found` → 改 create，create 报
  `already in use` → 改 resume；超时绝不回退（会话可能仍在工作中）。
- **dsh 后端**：headless profile 挂载本地插件 `headless-session`
  （`~/.dsh/profiles/headless/plugins/headless-session.js`），经环境变量
  `DSH_SESSION_ID` 复用持久化会话；不传时与一次性任务模式一致。

环境要点：
- **认证**：launchd 启动的桥接进程拿不到 `~/.zshrc` 里的
  `ANTHROPIC_AUTH_TOKEN`，必须写在 `config.json` 的 `claude_env` 里
  （BASE_URL / 模型由 `~/.claude/settings.json` 提供，无需重复配置）。
- **可执行文件解析**：`claude_bin` 留空时自动解析（PATH → `~/.local/bin/claude`，
  launchd 的 PATH 不含后者，靠此兜底到绝对路径）；`dsh_bin` 留空时
  PATH → npx 缓存扫描。
- 桥接工作目录（`workdir`）为独立飞书工作区 `~/Documents/feishu-workspace`。
- 权限：`claude_permission_mode`（默认 `bypassPermissions` → 传
  `--dangerously-skip-permissions`，其它值传 `--permission-mode`）；
  `dsh_permission_mode`（默认 `danger-full-access`）经 `DSH_PERMISSION_MODE`
  传给 headless。
- **桥接不负责删除会话**：「新会话」仅轮换映射 id；claude 会话文件在
  `~/.claude/projects/`（可用 `claude --resume <id>` 在终端打开），dsh 会话
  文件在 `~/.dsh/sessions/`（Web GUI 删除由社区插件
  `dsh-plugin-session-delete` 提供）。
- **空闲自动结束会话**：`.feishu_sessions.json` 记录各分区 `active_at`；距
  上次消息超过 `session_idle_timeout_min`（默认 0 = 不启用）时，下一条消息
  自动轮换全新会话 id；未超时沿用同一会话。

## 常用命令

- 项目位置：`/Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge`
- 安装依赖（开发模式）：`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- 运行：`cd <项目位置> && .venv/bin/python -m feishu_codex_bridge`
- 测试：`cd <项目位置> && .venv/bin/python -m pytest`
- 重启线上服务（LaunchAgent 托管，RunAtLoad + KeepAlive）：
  `launchctl kickstart -k gui/501/com.dsh.feishu-bridge`
  （plist 在 `~/Library/LaunchAgents/com.dsh.feishu-bridge.plist`；直接 `kill`
  会被 KeepAlive 重新拉起）
- 手工后台运行（不用 LaunchAgent 时）：
  `nohup bash -c 'exec .venv/bin/python -m feishu_codex_bridge' >> bridge.log 2>&1 &`
  （直接 `nohup .venv/bin/python -m ... &` 在本环境会被二次拉起成双进程；bridge 自带
  flock 单实例保护 `.bridge.pid`，重复进程会被拒绝）

## 目录结构

- `feishu_codex_bridge/`：主包
  - `config.py`：配置加载、`claude_bin`/`dsh_bin` 解析、启动自检、日志初始化
  - `feishu.py`：飞书客户端、消息解析、权限判断、消息发送与卡片渲染
  - `backend.py`：后端注册表（claude / dsh 元数据）与统一分派 `run_task`
  - `claude.py`：调用本机 `claude -p` headless（会话快路径 + 双向回退 + 超时）
  - `dsh.py`：调用本机 `dsh --profile headless` 执行一次性任务
  - `sessions.py`：读取 Codex 会话记录的遗留模块（当前未使用，保留参考）
  - `cleanup.py`：DSH 一次性会话清理遗留工具（bridge 不再调度，保留手动使用）
  - `bridge.py`：消息处理主逻辑、会话映射（按后端分区）、单实例、长连接入口
- `tests/`：单元测试（用模拟事件 / 假脚本，不依赖真实飞书服务）

## 约定

- `config.json` 含真实密钥（App Secret、`ANTHROPIC_AUTH_TOKEN`），已被
  `.gitignore` 排除，**绝不提交**，建议 `chmod 600`；配置以
  `config.example.json` 为模板，新增配置项需同步更新示例文件。
- `ANTHROPIC_AUTH_TOKEN` 只放本地 `config.json` 的 `claude_env`，绝不写进
  示例文件 / 文档 / LaunchAgent plist。
- 飞书事件结构：`P2ImMessageReceiveV1`，消息内容在 `event.event.message`，
  发送者在 `event.event.sender.sender_id`。
- 代码风格：遵循 PEP 8，函数有简短 docstring。
- 修改后必须运行 `pytest` 确认测试通过。
