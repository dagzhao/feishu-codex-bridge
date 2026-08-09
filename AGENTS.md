# AGENTS.md

## 项目简介

飞书机器人 → 本机 Codex 的桥接服务：用户在飞书发消息，服务调用 `codex exec`
执行任务，并把 Codex 的结果发回飞书。使用飞书官方长连接模式，无需公网服务器。

## 常用命令

- 安装依赖（开发模式）：`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- 运行：`.venv/bin/python -m feishu_codex_bridge`
- 测试：`.venv/bin/python -m pytest`
- 后台运行：`nohup .venv/bin/python -m feishu_codex_bridge >> bridge.log 2>&1 &`

## 目录结构

- `feishu_codex_bridge/`：主包
  - `config.py`：配置加载、日志初始化
  - `feishu.py`：飞书客户端、消息解析、权限判断、消息发送
  - `codex.py`：调用本机 codex exec
  - `bridge.py`：消息处理主逻辑与长连接入口
- `tests/`：单元测试（用模拟事件，不依赖真实飞书服务）

## 约定

- `config.json` 含真实密钥，已被 `.gitignore` 排除，**绝不提交**；配置以
  `config.example.json` 为模板，新增配置项需同步更新示例文件。
- 飞书事件结构：`P2ImMessageReceiveV1`，消息内容在 `event.event.message`，
  发送者在 `event.event.sender.sender_id`。
- 代码风格：遵循 PEP 8，函数有简短 docstring。
- 修改后必须运行 `pytest` 确认测试通过。
