# 飞书远程控制 DSH（桥接服务）

在飞书里给机器人发消息，自动调用本机 DSH（DeepSeek Harness）headless 模式
执行任务，并把结果发回飞书。无需公网 IP、无需域名、无需回调服务器（使用
飞书官方长连接）。

> 后端从 Codex 迁移到 DSH：每条消息 = 一个 `dsh --profile headless` 任务；
> 自 2026-08-17 起支持**固定会话续聊**（所有飞书消息共用一个持久化会话，
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
   - `dsh_bin`：DSH CLI 可执行文件路径。**可留空**——留空或路径失效时自动解析（优先 PATH 上的 `dsh`，其次扫描 `~/.npm/_npx/*/node_modules/.bin/dsh` 取最新）
   - `workdir`：DSH 执行任务的工作目录（当前为独立飞书工作区
     `~/Documents/feishu-workspace`，对应 Web GUI 左侧的「飞书」工作区；如需
     改回主工作区可填 `~/Documents/harnessWorkSpace`）
   - `dsh_permission_mode`：headless 沙箱文件策略，默认
     `danger-full-access`（全权限、无审批；headless 默认是 `workspace-write`，
     只允许写工作目录内——改到独立工作区后必须 full access 才能访问其他目录）
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

> 注意：项目在工作区（`~/Documents/harnessWorkSpace/feishu-bridge`）。macOS
> 隐私保护不允许 LaunchAgent 自启服务读取 Documents 目录，因此本项目**不再
> 使用开机自启**（旧的 `com.codex.feishu-bridge` LaunchAgent 已移除）；如需
> 开机自启，请给运行环境授予「完全磁盘访问权限」后自行注册 LaunchAgent，
> 或把代码放到 `~/` 下（与本仓库原设计一致）。
>
> 若使用 LaunchAgent（`com.dsh.feishu-bridge`）：必须在 plist 里通过
> `EnvironmentVariables` 设置完整 `PATH`（含 `/opt/homebrew/bin`），否则
> `dsh` 脚本的 `#!/usr/bin/env node` 找不到 node，任务会报
> `env: node: No such file or directory`（退出码 127）。

## 四、使用

在飞书里给机器人发消息，例如：

- `帮我看看路由器 10.33.10.31 是否在线`
- `查询系统状态`
- `整理一下项目目录结构`

机器人会先回复「🧠 收到指令，DSH 开始处理…」，任务完成后把 DSH 的结果发回来。

### 连接状态自动通知

飞书长连接偶尔会被网络断开（路由器/NAT 空闲超时等原因），断线期间发的消息
飞书不会补发。桥接服务现在会自动处理：

- 服务重启后，会向最近聊天的会话发一条「✅ 桥接服务已启动」确认
- 飞书连接中断超过 15 秒再恢复时，会通知你「🔌 连接已恢复（中断约 X 秒），
  刚才没收到回复的消息请重发」
- 短于 15 秒的闪断不打扰你（会自动重连，且重连期间的消息通常不受影响）

### 固定会话续聊（真·多轮上下文）

自 2026-08-17 起，DSH headless 支持通过环境变量 `DSH_SESSION_ID` 复用持久化
会话（`~/.dsh/sessions/`），实现**真正的多轮续聊**：

- 桥接层使用一个固定会话 id（`session-feishu-<hex>`，存 `.feishu_sessions.json`），
  **所有飞书消息共用一个会话**，模型能记住之前的对话内容（同一会话内串行
  处理，避免并发写入互相覆盖上下文）。
- **空闲自动结束会话**（2026-08-18 起）：距上次消息超过
  `session_idle_timeout_min`（默认 10 分钟）后，下一条消息自动轮换到全新
  会话 id（旧会话就此结束、上下文清空）；10 分钟内继续发消息则沿用同一会话。
- 发送「新会话」/「退出会话」/「重置会话」会删除该会话映射（轮换 id），
  下一次对话自动走全新会话（上下文清空）。
- 实现：headless profile 挂载本地插件
  `~/.dsh/profiles/headless/plugins/headless-session.js`（通过
  `cordis.patch.yml` 的 `insert` 注入并禁用默认 runner），无 session 时行为
  与原先的一次性模式完全一致。

### 会话删除交给 DSH（桥接不再删会话）

- 自 2026-08-18 起，feishu-bridge **不再负责删除任何会话**：移除了一次性会话
  自动清理调度；「新会话」只删除会话映射（轮换 id），旧会话文件保留在
  `~/.dsh/sessions/`，由 DSH 侧管理。
- DSH Web GUI 的会话删除由社区插件
  [`dsh-plugin-session-delete`](https://github.com/lsz-asd/dsh-plugin-session-delete)
  提供（已装进 web profile）：会话行菜单「删除」+ 头部危险按钮，删除时会清理
  会话日志、投影缓存与工作区记账。
- 遗留配置项 `session_cleanup_max_age_hours` / `session_cleanup_interval_hours`
  已不再使用，可保留可删除。

## 安全说明

- 机器人能调用本机 DSH 的全部能力，**务必填写 `allowed_open_ids` 白名单**。
- 不要把 `config.json`（含 App Secret）提交到任何公开仓库。
- 建议只在受信任的飞书账号/群内使用。
- 停止服务：找到进程后 `kill <pid>`，或用 `pkill -f feishu_codex_bridge`。
