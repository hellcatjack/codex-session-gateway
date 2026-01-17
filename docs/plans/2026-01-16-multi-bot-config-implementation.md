# Multi-Bot Config Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 引入 `config.toml` 拆分基础配置与 bot 关键绑定，并支持单进程多 bot 固定绑定到各自 Codex session。

**Architecture:** 新增 TOML 配置加载与 `${ENV:...}` 解析；`Config` 重构为 `BaseConfig + BotConfig` 组合；启动时为每个 bot 创建独立适配器/编排器实例，持久层增加 bot 维度隔离。

**Tech Stack:** Python 3.11 `tomllib`、现有 SQLite、Python-telegram-bot。

### Task 1: 新增配置结构与解析（含 env 占位解析）

**Files:**
- Create: `src/config_loader.py`
- Modify: `src/config.py`
- Test: `tests/test_config_loader.py`

**Step 1: 写失败测试（占位解析与字段校验）**

```python
from src.config_loader import load_toml_config, resolve_env_placeholders

def test_env_placeholder_resolve():
    text = "${ENV:TELEGRAM_BOT_TOKEN_1}"
    assert resolve_env_placeholders(text, {"TELEGRAM_BOT_TOKEN_1": "abc"}) == "abc"

def test_missing_required_fields(tmp_path):
    toml = """
    [base]
    db_path = "data/app.db"

    [[bots]]
    name = "bot-alpha"
    token = "token"
    allowed_user_ids = [1]
    """
    path = tmp_path / "config.toml"
    path.write_text(toml, encoding="utf-8")
    bots = load_toml_config(str(path))
    assert bots.errors  # 缺 resume_id/codex_workdir
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_loader.py::test_env_placeholder_resolve -v`
Expected: FAIL (函数不存在)

**Step 3: 实现解析逻辑**

实现：
- `BaseConfig`：基础设置
- `BotConfig`：bot 级绑定（name/token/allowed_user_ids/resume_id/codex_workdir + overrides）
- `load_config()` 返回 `AppConfig(bots=[...], base=...)`
- `resolve_env_placeholders()` 支持 `${ENV:KEY}`
- 未配置 `config.toml` 时保持向后兼容 `.env` 单 bot

**Step 4: 测试通过**

Run: `pytest tests/test_config_loader.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/config.py src/config_loader.py tests/test_config_loader.py
git commit -m "feat: add toml config loader for multi-bot"
```

### Task 2: 启动多 bot 与 session 隔离

**Files:**
- Modify: `src/main.py`
- Modify: `src/store.py`
- Modify: `src/models.py`
- Modify: `src/session_manager.py`
- Modify: `src/orchestrator.py`
- Modify: `src/adapters/telegram_adapter.py`
- Test: `tests/test_orchestrator.py`

**Step 1: 写失败测试（bot 隔离）**

```python
from src.models import Session
from src.store import Store

def test_bot_isolation(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    store.init()
    s1 = Session(user_id=1, bot_id="bot-a")
    s2 = Session(user_id=1, bot_id="bot-b")
    store.record_session(s1)
    store.record_session(s2)
    store.update_session_last_result(s1.session_id, "a")
    store.update_session_last_result(s2.session_id, "b")
    assert store.get_last_result_by_user_id(1, "bot-a") == "a"
    assert store.get_last_result_by_user_id(1, "bot-b") == "b"
```

**Step 2: 实现 bot 维度隔离**

- sessions 表新增 `bot_id` 列；查询/更新以 `(user_id, bot_id)` 为维度
- Orchestrator/SessionManager 增加 `bot_id` 入参
- TelegramAdapter 实例化时固定绑定一个 `bot_id`

**Step 3: 运行测试通过**

Run: `pytest tests/test_orchestrator.py::test_bot_isolation -v`
Expected: PASS

**Step 4: 提交**

```bash
git add src/main.py src/store.py src/models.py src/session_manager.py src/orchestrator.py src/adapters/telegram_adapter.py tests/test_orchestrator.py
git commit -m "feat: isolate sessions per bot"
```

### Task 3: 文档与示例配置更新

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Create: `config.toml.example`

**Step 1: 更新 README**

- 添加 `config.toml` 示例与多 bot 示例
- 明确 `codex_workdir` 为 bot 绑定关键项

**Step 2: 更新 `.env.example`**

- 拆分基础设置与 token 占位
- 增加 `TELEGRAM_BOT_TOKEN_1/2` 示例

**Step 3: 新增 `config.toml.example`**

包含 2 个 bot 的示例配置。

**Step 4: 提交**

```bash
git add README.md .env.example config.toml.example
git commit -m "docs: add multi-bot config examples"
```

### Task 4: 验证与收尾

**Step 1: 全量测试**

Run: `pytest`
Expected: PASS

**Step 2: 汇总变更**

```bash
git status -sb
git log --oneline -n 5
```
