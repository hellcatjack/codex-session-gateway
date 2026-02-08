# 常见问题与排障

## 重复发送“最终结果”消息

### 现象 A：任务结束后（“运行完成/等待新指令”）又晚到一条相同结果

原因：开启了 JSONL 同步轮询时，可能会在任务状态消息发送后，轮询到同一段 assistant 结果并再次推送。

现状：项目已增加“基于最近流式输出的去重”，如果 JSONL 轮询到的结果已在本次运行的流式输出中出现，则会跳过发送，并在日志中出现：
- `JSONL 去重：跳过重复结果`

### 现象 B：同一次运行中，结果已逐行流式输出完毕，随后又追加一整块完全相同的结果

原因：Codex CLI 会把“最后一条消息”写入 `--output-last-message` 文件。若同时 stdout 已逐行输出了同样内容，网关在进程正常退出时再次发送“整块最终消息”就会导致重复。

现状：项目已修复该重复发送逻辑：当检测到最终消息已在本次流式输出中出现时，只记录 `on_final`，不再重复推送整块内容。

### 自查清单（仍出现重复时）

1. 确认只运行了一个服务实例（避免多个进程同时 polling 同一个 bot）：
   - `systemctl status codex-session-gateway --no-pager`
   - `ps -eo pid,cmd | rg "python -m src\\.main"`
2. 多 bot 场景下，确认每个 bot 的 token 不相同（避免同一 token 被多个线程/进程 polling）：
   - 检查 `config.toml` 的 `bots[].token` 对应的环境变量是否指向不同值
3. 观察服务日志中是否有去重命中：
   - `sudo journalctl -u codex-session-gateway -f`
4. 复现时记录信息（用于精确定位）：
   - 哪个 bot（`stock/gateway/comfyUI`）
   - 发生时间点（精确到分钟）
   - 重复表现：是“同一条消息被编辑追加两次”，还是“发了两条独立消息”

## 启动日志出现 JobQueue 警告

日志示例：
- `PTBUserWarning: No JobQueue set up...`

说明：当前环境未安装 `python-telegram-bot[job-queue]` 额外依赖，网关会自动回退到“内置轮询”的 JSONL 同步，不影响基本功能。

如果想消除该警告并启用 JobQueue，可改用带 extras 的安装方式（并同步更新依赖管理策略）：
```bash
pip install "python-telegram-bot[job-queue]"
```

## 如何确认当前运行代码版本

1. 查看仓库提交：
```bash
cd /app/codexTelegramShell
git rev-parse HEAD
git show -s --format=%ci HEAD
```

2. 查看服务启动时间（确认重启后已加载新代码）：
```bash
systemctl status codex-session-gateway --no-pager
```

## 如何查看当前自动选择的 Session

- 在 Telegram 里发送 `/status`，输出中会包含当前解析后的 `resume_id`（`auto` 模式会显示实际 session id）。
- 若显示为 `未设置`，通常表示该 `workdir` 下暂时还没有可用的主会话（`~/.codex/sessions` 中找不到匹配记录）；先在该目录运行一次 Codex CLI 创建会话后再重试。

## 自测（本地单元测试）

```bash
cd /app/codexTelegramShell
.venv/bin/python -m pytest -q
```
