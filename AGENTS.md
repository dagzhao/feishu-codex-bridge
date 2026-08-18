# AGENTS.md

## 项目简介

飞书机器人 → 本机 DSH（DeepSeek Harness）的桥接服务：用户在飞书发消息，
服务调用 `dsh --profile headless` 执行任务，并把 DSH 的结果发回飞书。
使用飞书官方长连接模式，无需公网服务器。自 2026-08-17 起支持**固定会话
续聊**：headless profile 挂载本地插件 `headless-session`
（`~/.dsh/profiles/headless/plugins/headless-session.js`），桥接层使用一个
固定会话 id（**所有飞书消息共用一个会话**，不区分用户）并通过环境变量
`DSH_SESSION_ID` 传给 dsh，消息复用同一持久化会话（真·多轮上下文）；
「新会话」命令删除映射（轮换 id），下次对话走全新上下文，旧会话文件
由 DSH 侧管理（桥接不删会话文件）。
不传 `DSH_SESSION_ID` 时行为与原先的一次性任务模式一致。

自 2026-08-18 起：
- 桥接工作目录（`config.json` 的 `workdir`）指向独立飞书工作区
  `~/Documents/feishu-workspace`（已注册为 Web GUI 左侧的「飞书」工作区，
  飞书会话会归到该工作区下而非「未分组」）。
- `config.json` 的 `dsh_permission_mode`（默认 `danger-full-access`）通过
  `DSH_PERMISSION_MODE` 环境变量传给 headless，覆盖其默认的
  `workspace-write` 沙箱：飞书任务拥有全文件访问、无审批（headless 默认
  只允许写工作目录内，改到新工作区后必须 full access 才能继续访问其他目录）。
- **桥接不负责删除会话**（2026-08-18 起）：移除了自动清理调度；「新会话」
  仅删除映射轮换 id，旧会话文件留在 `~/.dsh/sessions/` 由 DSH 侧管理。
  Web GUI 的逐会话删除由社区插件 `dsh-plugin-session-delete`
  （`~/.dsh/profiles/web` 已安装，`github:lsz-asd/dsh-plugin-session-delete`）
  提供：会话行菜单删除 + 头部危险按钮，清理日志/投影缓存/工作区记账。
- **空闲自动结束会话**（2026-08-18 起）：`.feishu_sessions.json` 记录
  `shared_active_at`；距上次消息超过 `session_idle_timeout_min`（默认 10 分钟），
  下一条消息自动轮换全新会话 id（旧会话结束）；未超时沿用同一会话。

## 常用命令

- 项目位置：`/Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge`
- 安装依赖（开发模式）：`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- 运行：`cd <项目位置> && .venv/bin/python -m feishu_codex_bridge`
- 测试：`cd <项目位置> && .venv/bin/python -m pytest`
- 手动后台运行：`nohup bash -c 'exec .venv/bin/python -m feishu_codex_bridge' >> bridge.log 2>&1 &`
  （直接 `nohup .venv/bin/python -m ... &` 在本环境会被二次拉起成双进程；bridge 自带
  flock 单实例保护 `.bridge.pid`，重复进程会被拒绝）
- 无开机自启（工作区在 Documents 下，macOS TCC 限制 LaunchAgent 读取）

## 目录结构

- `feishu_codex_bridge/`：主包
  - `config.py`：配置加载、日志初始化
  - `feishu.py`：飞书客户端、消息解析、权限判断、消息发送
  - `dsh.py`：调用本机 `dsh --profile headless` 执行一次性任务
  - `sessions.py`：读取 Codex 会话记录的遗留模块（当前未使用，保留参考）
  - `bridge.py`：消息处理主逻辑、简单命令与长连接入口
- `tests/`：单元测试（用模拟事件，不依赖真实飞书服务）

## 约定

- `config.json` 含真实密钥，已被 `.gitignore` 排除，**绝不提交**；配置以
  `config.example.json` 为模板，新增配置项需同步更新示例文件。
- 飞书事件结构：`P2ImMessageReceiveV1`，消息内容在 `event.event.message`，
  发送者在 `event.event.sender.sender_id`。
- 代码风格：遵循 PEP 8，函数有简短 docstring。
- 修改后必须运行 `pytest` 确认测试通过。
