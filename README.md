# Codex Session Gateway

用于在内网单机环境中，通过 Telegram Bot 绑定指定 Session ID，与本地 Codex CLI 实时交互的 Python 服务。

## 功能
- Telegram 实时对话与流式输出
- 任务运行状态提示与取消
- 指令排队与重试
- 本地 SQLite 记录会话与运行日志

## 运行环境
- Python 3.11+
- 本地可执行的 Codex CLI

## 说明
- 为避免 `The cursor position could not be read within a normal duration`，运行 Codex CLI 时默认设置 `PROMPT_TOOLKIT_NO_CPR=1` 与 `TERM=xterm-256color`。
- 当设置 `CODEX_CLI_RESUME_ID` 时，默认使用 `codex exec resume <id>`（非交互模式）以保证 Telegram 场景可稳定输出。

## 安装依赖
```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 配置
系统会优先读取 `config.toml`；不存在时回退到 `.env`（单 bot 兼容）。

### config.toml（推荐，多 bot）
- `base`：基础设置（全局共享）
- `bots`：每个 bot 一条记录，必须包含 `name / token / allowed_user_ids / resume_id / codex_workdir`
- 支持 `${ENV:KEY}` 占位符，便于把敏感信息放到 `.env`

示例（可参考 `config.toml.example`）：

```toml
[base]
db_path = "data/app.db"
lock_path = "data/app.lock"
codex_cli_cmd = "codex"
codex_cli_input_mode = "stdin"
jsonl_sync_interval_seconds = 3
message_chunk_limit = 3500

[[bots]]
name = "primary"
token = "${ENV:TELEGRAM_BOT_TOKEN_PRIMARY}"
allowed_user_ids = [123456789]
resume_id = "${ENV:CODEX_CLI_RESUME_ID_PRIMARY}"
codex_workdir = "${ENV:CODEX_WORKDIR_PRIMARY}"

[[bots]]
name = "backup"
token = "${ENV:TELEGRAM_BOT_TOKEN_BACKUP}"
allowed_user_ids = [123456789, 11223344]
resume_id = "${ENV:CODEX_CLI_RESUME_ID_BACKUP}"
codex_workdir = "${ENV:CODEX_WORKDIR_BACKUP}"
```

### .env（兼容单 bot）
至少需要配置：
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `CODEX_CLI_RESUME_ID`
- `CODEX_WORKDIR`

其余基础设置可参考 `.env.example`（与 `base` 字段一致）。

基础设置字段说明（`base` 或 `.env` 通用）：
- `CODEX_CLI_CMD`：Codex CLI 命令（默认 `codex`）
- `CODEX_CLI_ARGS`：Codex CLI 额外参数（可选）
- `CODEX_CLI_INPUT_MODE`：`stdin` 或 `arg`（默认 `stdin`）
- `CODEX_CLI_APPROVALS_MODE`：审批模式（默认 `3`，等价于输入 `/approvals 3`）
- `CODEX_CLI_SKIP_GIT_CHECK`：是否跳过 Git 仓库检查（`1/0`，默认 `1`，用于 `codex exec`）
- `CODEX_CLI_USE_PTY`：是否使用伪终端（`1/0`，默认 `0`，用于解决 `stdin is not a terminal`）
- `DB_PATH`：SQLite 路径（默认 `data/app.db`）
- `LOCK_PATH`：进程锁文件路径（默认 `data/app.lock`，用于防止多重启动）
- `STREAM_FLUSH_INTERVAL`：输出节流间隔秒数（默认 `1.5`）
- `STREAM_INCLUDE_STDERR`：是否显示 stderr（`1/0`，默认 `0`）
- `PROGRESS_TICK_INTERVAL`：进度探针间隔秒数（默认 `15`，设置为 `0` 可关闭等待提示）
- `RUN_TIMEOUT_SECONDS`：单次运行超时秒数（默认 `900`）
- `CONTEXT_COMPACTION_IDLE_TIMEOUT_SECONDS`：检测到 `Context compacted` 后无新输出的等待秒数（默认 `60`，用于防止进程卡住）
- `NO_OUTPUT_IDLE_TIMEOUT_SECONDS`：长时间无任何输出时的自动结束秒数（默认 `900`，用于防止僵死进程）
- `FINAL_RESULT_IDLE_TIMEOUT_SECONDS`：检测到最终结果后无输出的自动结束秒数（默认 `30`，用于防止任务卡住）
- `JSONL_SYNC_INTERVAL_SECONDS`：JSONL 同步轮询间隔秒数（默认 `3`，用于同步本地 CLI 的结果）
- `CODEX_JSONL_STREAM_EVENTS`：是否从 Codex JSONL 实时推送事件到 Telegram（`1/0`，默认 `1`，`agent_reasoning` 内容会被隐藏）
- `CODEX_JSONL_REASONING_THROTTLE_SECONDS`：推送 `agent_reasoning` 占位提示的最小间隔秒数（默认 `10`）
- `CODEX_JSONL_REASONING_MODE`：推理事件展示模式，`hidden`（仅占位提示）或 `summary`（安全摘要，默认 `hidden`）
- `MESSAGE_CHUNK_LIMIT`：单条消息最大长度（默认 `3500`，实际会被限制在 4096 以内，并自动拆分）

## 安全与审计
- 安全与敏感信息规范：`docs/security.md`
- 最近一次审计报告：`docs/audits/2026-01-16-security-audit.md`

## 启动
```bash
python -m src.main
```

## systemd 服务
项目内提供服务文件模板：`deploy/codex-session-gateway.service`。请按实际路径修改后安装（已默认使用 `.venv` 里的 Python）。
如需在 Codex 指令中调用 `systemctl --user`，请确保用户 DBus 可用（服务模板已设置 `XDG_RUNTIME_DIR` 与 `DBUS_SESSION_BUS_ADDRESS`）。
若仍提示 `Failed to connect to bus: No medium found`，请启用用户常驻：`loginctl enable-linger <用户>`。

安装示例：
```bash
sudo cp deploy/codex-session-gateway.service /etc/systemd/system/codex-session-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now codex-session-gateway
```

查看日志：
```bash
sudo journalctl -u codex-session-gateway -f
```

## 可用指令
- `/new <内容>`：提交新指令
- `/session`：查看会话绑定（只读）
- `/stop`：停止当前任务
- `/status`：查看状态
- `/retry`：重试上一次指令
- `/lastresult`：查看最近一次结果
- `/whoami`：查看用户与聊天 ID
- `/help`：查看帮助

## 测试
```bash
pytest
```
