# 飞书远程控制桥接服务（Claude Code / DSH 双后端）

在飞书里给机器人发消息，自动调用本机 **Claude Code**（`claude -p` headless
模式）执行任务，并把结果发回飞书。无需公网 IP、无需域名、无需回调服务器
（使用飞书官方长连接）。

> 后端演进：Codex → DSH（`dsh --profile headless`）→ **Claude Code**（当前
> 默认）。通过 `config.json` 的 `backend` 开关可在 `claude` / `dsh` 之间
> 切换；所有后端都支持**固定会话续聊**（所有飞书消息共用一个持久化会话，
> 真·多轮上下文），见下文「固定会话续聊」。

## 一、飞书开放平台配置（一次）

完整图文流程见 **[FEISHU_SETUP.md](FEISHU_SETUP.md)**，摘要如下：

1. 打开 <https://open.feishu.cn> 登录（个人可免费创建团队）。
2. 创建**企业自建应用**，启用**机器人**能力。
3. 在「凭证与基础信息」复制 **App ID** 和 **App Secret**。
4. 在「权限管理」开通 `im:message`、`im:message:send_as_bot`（可选 `im:chat:readonly`）。
5. 在「事件与回调」选择**长连接模式**，添加事件 `im.message.receive_v1`。
6. 在「版本管理与发布」创建版本并发布（自建企业本人审批，即时通过）。
7. 在飞书里搜索应用名，进入单聊并获取自己的 open_id（见 FEISHU_SETUP.md 第 8 步）。

## 二、本地配置

1. 编辑 `config.json`：
   - `app_id` / `app_secret`：填上一步拿到的值
   - `allowed_open_ids`：允许使用机器人的飞书用户 open_id（留空数组 = 所有人可用，**强烈建议填写**）
   - `allowed_chat_ids`：允许使用的群 chat_id（可选，留空则不限制群）
   - `backend`：后端选择，`"claude"`（默认）或 `"dsh"`；未知值回落到 claude
   - `claude_bin`：claude CLI 可执行文件路径。**可留空**——留空或路径失效时
     自动解析（优先 PATH 上的 `claude`，其次 `~/.local/bin/claude`，即 native
     安装位置）
   - `claude_env`：注入 claude 子进程的额外环境变量（dict）。**由 launchd
     启动的桥接进程拿不到 `~/.zshrc` 里的 `ANTHROPIC_AUTH_TOKEN`，必须在这里
     填写**，否则 claude 调用会认证失败（BASE_URL / 模型由
     `~/.claude/settings.json` 提供，无需重复配置）
   - `claude_permission_mode`：默认 `bypassPermissions`（映射为
     `--dangerously-skip-permissions`，无人值守全放行）；填其它值则传
     `--permission-mode <值>`（如 `acceptEdits`、`plan`）
   - `claude_model`：claude 模型名（可选，**留空 = 用 claude CLI 当前默认
     模型**）
   - `dsh_bin`：DSH CLI 可执行文件路径（仅 backend=dsh 时使用；可留空，留空
     或路径失效时自动解析：优先 PATH 上的 `dsh`，其次扫描
     `~/.npm/_npx/*/node_modules/.bin/dsh` 取最新）
   - `workdir`：任务执行的工作目录（当前为独立飞书工作区
     `~/Documents/feishu-workspace`）
   - `dsh_permission_mode`：dsh headless 沙箱文件策略，默认
     `danger-full-access`（仅 backend=dsh 时使用）
   - `session_idle_timeout_min`：会话空闲自动轮换时间（分钟），默认 0 =
     永不因空闲轮换——**只延续旧会话，除非消息是「新会话」命令**；如想恢复
     旧的空闲自动结束行为，设 > 0（超过该时长没新消息，下一条消息开新会话）
   - `task_timeout_sec`：单个任务超时（秒），默认 300
2. 获取你自己的 open_id：
   - 启动服务后，在飞书给机器人发任意消息，查看 `bridge.log` 里 `拒绝未授权消息: open_id=xxx` 的日志；或
   - 在飞书开放平台「通讯录」接口查询自己的 user_id/open_id。
3. 把 open_id 填入 `allowed_open_ids`，保存。

## 三、启动服务

首次安装依赖（已装好可跳过）：

```bash
cd /Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

启动：

```bash
cd /Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge
.venv/bin/python -m feishu_codex_bridge
```

保持后台运行（手动）：

```bash
cd /Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge
nohup bash -c 'exec .venv/bin/python -m feishu_codex_bridge' >> bridge.log 2>&1 &
```

> 注意：直接 `nohup .venv/bin/python -m ... &` 在本机环境下偶尔会被二次拉起
> 成两个进程（飞书消息会重复处理）。请用上面的 `bash -c 'exec ...'` 包装形式；
> 另外 bridge 自带 flock 单实例保护（`.bridge.pid`），重复进程会被拒绝退出。

运行测试：

```bash
cd /Users/zhaojingtao/Documents/harnessWorkSpace/feishu-bridge && .venv/bin/python -m pytest
```

> 实际部署（本机）：服务由 LaunchAgent `com.dsh.feishu-bridge` 托管
> （RunAtLoad + KeepAlive），plist 在
> `~/Library/LaunchAgents/com.dsh.feishu-bridge.plist`。改代码 / 配置后重启：
>
> ```bash
> launchctl kickstart -k gui/501/com.dsh.feishu-bridge
> ```
>
> 注意 plist 的 `EnvironmentVariables` 只设了基础 `PATH`（不含 `~/.local/bin`，
> 也不含 zshrc 的任何变量）——所以 claude 可执行文件靠自动解析到
> `~/.local/bin/claude` 的绝对路径，`ANTHROPIC_AUTH_TOKEN` 靠 `config.json`
> 的 `claude_env` 注入，都不依赖 launchd 环境变量。
>
> 若用手工方式（无 LaunchAgent）：macOS 隐私保护不允许 LaunchAgent 自启服务
> 读取 Documents 目录，如遇 TCC 问题请给运行环境授予「完全磁盘访问权限」，
> 或把代码放到 `~/` 下（与本仓库原设计一致）。

## 四、使用

在飞书里给机器人发消息，例如：

- `帮我看看路由器 10.33.10.31 是否在线`
- `查询系统状态`
- `整理一下项目目录结构`

机器人会先回复「🧠 收到指令，Claude 开始处理…」，任务完成后把结果以卡片发回来（卡片标题「🤖 Claude 执行结果」，按后端显示为 DSH）。

### 连接状态自动通知

飞书长连接偶尔会被网络断开（路由器/NAT 空闲超时等原因），断线期间发的消息
飞书不会补发。桥接服务现在会自动处理：

- 服务重启后，会向最近聊天的会话发一条「✅ 桥接服务已启动」确认
- 飞书连接中断超过 15 秒再恢复时，会通知你「🔌 连接已恢复（中断约 X 秒），
  刚才没收到回复的消息请重发」
- 短于 15 秒的闪断不打扰你（会自动重连，且重连期间的消息通常不受影响）

### 固定会话续聊（真·多轮上下文）

桥接层为每个后端维护一个固定会话 id（存 `.feishu_sessions.json`，**按后端
分区**：`sessions.claude` / `sessions.dsh`），**所有飞书消息共用一个会话**，
模型能记住之前的对话内容（同一会话内串行处理，避免并发写入互相覆盖上下文）。
切换 `backend` 时两个分区各自保留，切回来仍能延续旧后端的上下文。

- **claude 后端**：会话 id 是 UUID。首次消息用 `--session-id` 创建会话，之后
  用 `--resume` 续聊；会话文件在 `~/.claude/projects/<工作目录转义>/`。会话
  文件被外部删除时自动回退重建，不会中断对话。
- **dsh 后端**：沿用 `DSH_SESSION_ID` 环境变量 + 本地插件
  `~/.dsh/profiles/headless/plugins/headless-session.js`（通过
  `cordis.patch.yml` 注入），会话文件在 `~/.dsh/sessions/`。
- **空闲自动结束会话**：距上次消息超过 `session_idle_timeout_min`（默认 0 =
  不启用）后，下一条消息自动轮换到全新会话 id（旧会话就此结束、上下文清空）；
  未超时则沿用同一会话。
- 发送「新会话」/「退出会话」/「重置会话」会轮换**当前后端**的会话 id，
  下一次对话自动走全新上下文（另一个后端的分区不受影响）。

### 会话删除（桥接不删任何会话）

- feishu-bridge **不负责删除任何会话文件**：「新会话」只轮换会话映射 id，
  旧会话文件保留在各自后端的存储里，由对应侧管理。
- claude 会话文件在 `~/.claude/projects/<工作目录转义>/`，可以用
  `claude --resume <id>` 在终端里直接打开继续聊（也可以从 Claude Code 的
  会话列表里查看/管理）。
- dsh 会话文件在 `~/.dsh/sessions/`，Web GUI 的逐会话删除由社区插件
  [`dsh-plugin-session-delete`](https://github.com/lsz-asd/dsh-plugin-session-delete)
  提供（已装进 web profile）：会话行菜单「删除」+ 头部危险按钮。
- 遗留配置项 `session_cleanup_max_age_hours` / `session_cleanup_interval_hours`
  已不再使用，可保留可删除。

## 安全说明

- 机器人能调用本机 Claude Code / DSH 的全部能力（含文件读写），**务必填写
  `allowed_open_ids` 白名单**。
- 不要把 `config.json` 提交到任何公开仓库：它含 App Secret 与
  `ANTHROPIC_AUTH_TOKEN`，建议 `chmod 600 config.json`（已在 .gitignore 中）。
- 建议只在受信任的飞书账号/群内使用。
- 停止服务：`launchctl bootout gui/501/com.dsh.feishu-bridge`（LaunchAgent
  托管时直接 `kill` 会被 KeepAlive 重新拉起）；手工部署的用
  `pkill -f feishu_codex_bridge`。
