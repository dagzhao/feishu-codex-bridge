# 飞书远程控制 Codex（桥接服务）

在飞书里给机器人发消息，自动调用本机 Codex 执行任务，并把结果发回飞书。
无需公网 IP、无需域名、无需回调服务器（使用飞书官方长连接）。

## 一、飞书开放平台配置（一次）

1. 打开 <https://open.feishu.cn>，用你的飞书账号登录，进入「开发者后台」。
2. 创建**企业自建应用**（名称随意，例如 `Codex 远程助手`）。
3. 进入应用 →「添加应用能力」→ 启用**机器人**能力。
4. 进入「凭证与基础信息」，复制 **App ID**（形如 `cli_xxx`）和 **App Secret**。
5. 进入「权限管理」，开通以下权限（点击「开通」并确认）：
   - `im:message`（读取用户发给机器人的单聊消息）
   - `im:message:send_as_bot`（以机器人身份发送消息）
   - `im:chat:readonly`（读取群组信息，可选，群聊白名单需要）
6. 进入「事件与回调」，在事件订阅里选择 **长连接模式**，并添加事件：
   - `im.message.receive_v1`（接收消息）
7. 进入「版本管理与发布」，创建版本并**申请发布**（自建企业由你本人审批，通常即时通过）。
8. 在飞书里搜索你的应用名，给机器人发一条消息，确认能收到回复「请先在服务端完成配置」之类即可（此时服务未启动）。

## 二、本地配置

1. 编辑 `config.json`：
   - `app_id` / `app_secret`：填上一步拿到的值
   - `allowed_open_ids`：允许使用机器人的飞书用户 open_id（留空数组 = 所有人可用，**强烈建议填写**）
   - `allowed_chat_ids`：允许使用的群 chat_id（可选，留空则不限制群）
   - `codex_bin`：Codex 可执行文件路径（默认指向桌面版内置路径）
   - `workdir`：Codex 执行任务的工作目录
   - `task_timeout_sec`：单个任务超时（秒），默认 300
2. 获取你自己的 open_id：
   - 启动服务后，在飞书给机器人发任意消息，查看 `bridge.log` 里 `拒绝未授权消息: open_id=xxx` 的日志；或
   - 在飞书开放平台「通讯录」接口查询自己的 user_id/open_id。
3. 把 open_id 填入 `allowed_open_ids`，保存。

## 三、启动服务

首次安装依赖（已装好可跳过）：

```bash
cd feishu-bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

启动：

```bash
.venv/bin/python bridge.py
```

保持后台运行：

```bash
nohup .venv/bin/python bridge.py >> bridge.log 2>&1 &
```

## 四、使用

在飞书里给机器人发消息，例如：

- `帮我看看路由器 10.33.10.31 是否在线`
- `查询系统状态`
- `整理一下项目目录结构`

机器人会先回复「🧠 收到指令…」，任务完成后把 Codex 的结果发回来。

## 安全说明

- 机器人能调用本机 Codex 的全部能力，**务必填写 `allowed_open_ids` 白名单**。
- 不要把 `config.json`（含 App Secret）提交到任何公开仓库。
- 建议只在受信任的飞书账号/群内使用。
- 停止服务：找到进程后 `kill <pid>`，或用 `pkill -f bridge.py`。
