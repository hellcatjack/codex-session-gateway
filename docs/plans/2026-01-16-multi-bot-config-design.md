# Codex Session Gateway 多 Bot 配置与绑定设计

**目标**
- 将配置拆分为“基础设置”与“Telegram + Codex 关键绑定”，提升可读性与可维护性。
- 支持单进程多 Telegram Bot，每个 bot 固定绑定一个 Codex session（resume_id + codex_workdir）。
- 保持敏感信息可读可管，支持 `${ENV:...}` 占位解析。

---

## 1. 配置结构与分层

采用 `config.toml` 作为主配置文件，结构如下：

- `[base]`：基础设置（对所有 bot 生效）
  - `db_path`、`stream_flush_interval`、`run_timeout_seconds`、`jsonl_sync_interval_seconds`、`message_chunk_limit` 等
- `[[bots]]`：每个 bot 的关键绑定
  - `name`、`token`、`allowed_user_ids`、`resume_id`、`codex_workdir`
  - 可选：`codex_cli_args` 等 bot 级覆盖字段

**`codex_workdir` 必须在 bot 级配置中出现**，确保“一个 bot 对应一个工作目录与 session”。

### 环境变量占位
为了兼顾安全与可读性，`token` 等敏感字段支持：

```
token = "${ENV:TELEGRAM_BOT_TOKEN_1}"
```

加载流程：先读取 `.env` → 解析 `config.toml` → 发现 `${ENV:...}` 时替换为环境变量值。

---

## 2. 多 Bot 运行与固定绑定

- 单进程内创建多个 `TelegramAdapter` 实例，依据 `[[bots]]` 配置列表启动。
- 固定绑定规则：每个 bot 的 `resume_id` 与 `codex_workdir` 不可变，配置中明确指定。
- 为避免状态污染，`SessionManager` / `Store` 引入 bot 维度隔离（如 session_key = bot_name + user_id）。
- JSONL 同步时按 bot 的 `resume_id` 与 `codex_workdir` 读取对应文件；不同 bot 互不干扰。
- 同一 bot 仍保持单任务队列；不同 bot 可并行运行。

---

## 3. 错误处理、可观测性与测试

- 启动时校验 `config.toml` 结构，缺少关键字段（`name`、`token`、`allowed_user_ids`、`resume_id`、`codex_workdir`）时仅跳过该 bot，不影响其他 bot。
- `${ENV:...}` 解析失败时提示缺失环境变量名，并将该 bot 标记为不可用。
- 日志输出包含 bot 上下文（如 `bot=alpha`），便于排查重复发送、JSONL 同步问题。

**测试建议**：
- 配置解析单测：base + bots 合并、占位解析、字段缺失降级。
- bot 级隔离单测：不同 bot 的 `last_result/jsonl_state` 互不影响。
- JSONL 同步单测：多 resume_id 不串线。

---

## 配置示例（摘要）

```toml
[base]
db_path = "data/app.db"
jsonl_sync_interval_seconds = 3
message_chunk_limit = 3500

[[bots]]
name = "bot-alpha"
token = "${ENV:TELEGRAM_BOT_TOKEN_1}"
allowed_user_ids = [123456789]
resume_id = "resume-xxxx-xxxx"
codex_workdir = "/app/project-alpha"

[[bots]]
name = "bot-beta"
token = "${ENV:TELEGRAM_BOT_TOKEN_2}"
allowed_user_ids = [123456789]
resume_id = "abcd-efgh-..."
codex_workdir = "/app/another-project"
```
