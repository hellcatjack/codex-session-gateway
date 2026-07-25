# Gateway Systemd Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the five-bot gateway only after the network is online and restart the whole service whenever any bot thread or the main process exits.

**Architecture:** Each adapter remains in its own daemon thread, but a thread-safe queue reports the first adapter termination to the main thread. The main thread converts every adapter return or exception into a non-zero process exit, while systemd orders startup after `network-online.target` and uses `Restart=always` to recreate the process.

**Tech Stack:** Python 3.10, `threading`, `queue`, pytest 8, python-telegram-bot 20.7, systemd, TOML and dotenv configuration.

---

### Task 1: Supervise Bot Adapter Threads

**Files:**
- Modify: `src/main.py:1-55`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Add failing tests for adapter return and exception**

Add tests that exercise the real supervisor helpers without starting Telegram:

```python
import queue


def test_adapter_normal_return_causes_gateway_failure():
    from src import main as main_mod

    exits = queue.Queue()
    adapter = type("ReturningAdapter", (), {"run": lambda self: None})()

    main_mod._run_adapter("stock", adapter, exits)

    with pytest.raises(RuntimeError, match="stock.*unexpectedly"):
        main_mod._raise_on_adapter_exit(exits)


def test_adapter_exception_causes_gateway_failure():
    from src import main as main_mod

    exits = queue.Queue()
    failure = ValueError("telegram startup failed")

    class FailingAdapter:
        def run(self):
            raise failure

    main_mod._run_adapter("trader", FailingAdapter(), exits)

    with pytest.raises(RuntimeError, match="trader.*failed") as exc_info:
        main_mod._raise_on_adapter_exit(exits)
    assert exc_info.value.__cause__ is failure
```

Update the existing `FakeThread` to accept `args`, and monkeypatch
`_raise_on_adapter_exit` to return in the session-manager isolation test so that
the test does not block on a synthetic thread.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_main.py
```

Expected: the two new tests fail because `_run_adapter` and
`_raise_on_adapter_exit` do not exist.

- [ ] **Step 3: Implement minimal thread supervision**

Add to `src/main.py`:

```python
import logging
import queue
from typing import Optional


_logger = logging.getLogger(__name__)
AdapterExitQueue = queue.Queue[tuple[str, Optional[BaseException]]]


def _run_adapter(bot_name: str, adapter: TelegramAdapter, exits: AdapterExitQueue) -> None:
    try:
        adapter.run()
    except BaseException as exc:
        _logger.exception("Bot adapter failed bot_id=%s", bot_name)
        exits.put((bot_name, exc))
        return
    exits.put((bot_name, None))


def _raise_on_adapter_exit(exits: AdapterExitQueue) -> None:
    bot_name, error = exits.get()
    if error is None:
        raise RuntimeError(f"Bot adapter exited unexpectedly bot_id={bot_name}")
    raise RuntimeError(f"Bot adapter failed bot_id={bot_name}") from error
```

In `main()`, create one exit queue, start every thread with the wrapper and
arguments `(bot.name, adapter, exits)`, then call `_raise_on_adapter_exit(exits)`
instead of joining threads sequentially.

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_main.py
.venv/bin/pytest -q
```

Expected: all main tests pass, followed by the complete suite passing.

- [ ] **Step 5: Commit thread supervision**

```bash
git add src/main.py tests/test_main.py
git commit -m "fix: restart gateway when a bot exits"
```

### Task 2: Enforce Network-Online and Restart Policy

**Files:**
- Modify: `deploy/codex-session-gateway.service:1-20`
- Create: `tests/test_systemd_service.py`

- [ ] **Step 1: Add a failing unit-file policy test**

Create `tests/test_systemd_service.py`:

```python
from pathlib import Path


def test_gateway_waits_for_network_and_always_restarts() -> None:
    unit = Path("deploy/codex-session-gateway.service").read_text(encoding="utf-8")

    assert "Wants=network-online.target" in unit
    assert "After=network-online.target" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
```

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_systemd_service.py
```

Expected: failure because the unit still uses `network.target` and
`Restart=on-failure`.

- [ ] **Step 3: Update the repository systemd unit**

Change the unit to:

```ini
[Unit]
Description=Codex Session Gateway
Wants=network-online.target
After=network-online.target

[Service]
Restart=always
RestartSec=5
```

Keep all existing user, working-directory, environment, logging, rate-limit and
install settings unchanged.

- [ ] **Step 4: Run policy and full tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_systemd_service.py
.venv/bin/pytest -q
```

Expected: both commands pass.

- [ ] **Step 5: Commit the unit policy**

```bash
git add deploy/codex-session-gateway.service tests/test_systemd_service.py
git commit -m "fix: make gateway service self-healing"
```

### Task 3: Integrate the Trader Bot Configuration

**Files:**
- Modify: `.env.example`
- Modify: `config.toml.example`
- Modify: `tests/test_config_loader.py`
- Modify locally only: `.env`
- Modify locally only: `config.toml`

- [ ] **Step 1: Add a failing example-configuration test**

Add to `tests/test_config_loader.py`:

```python
def test_example_config_includes_trader_bot():
    env = {
        "TELEGRAM_BOT_TOKEN_STOCK": "stock-token",
        "CODEX_WORKDIR_STOCK": "/tmp/stock",
        "TELEGRAM_BOT_TOKEN_GATEWAY": "gateway-token",
        "CODEX_WORKDIR_GATEWAY": "/tmp/gateway",
        "TELEGRAM_BOT_TOKEN_COMFYUI": "comfy-token",
        "CODEX_WORKDIR_COMFYUI": "/tmp/comfy",
        "TELEGRAM_BOT_TOKEN_TRADER": "trader-token",
        "CODEX_CLI_RESUME_ID_TRADER": "auto",
        "CODEX_WORKDIR_TRADER": "/tmp/trader",
    }

    result = load_toml_config("config.toml.example", env)

    assert result.errors == []
    trader = next(bot for bot in result.app_config.bots if bot.name == "trader")
    assert trader.token == "trader-token"
    assert trader.resume_id == "auto"
    assert trader.codex_workdir == "/tmp/trader"
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_config_loader.py::test_example_config_includes_trader_bot
```

Expected: failure because no bot named `trader` exists in the example.

- [ ] **Step 3: Add tracked trader placeholders**

Append to `.env.example`:

```dotenv
TELEGRAM_BOT_TOKEN_TRADER=
CODEX_CLI_RESUME_ID_TRADER=auto
CODEX_WORKDIR_TRADER=/app/trader
```

Append to `config.toml.example`:

```toml
[[bots]]
name = "trader"
token = "${ENV:TELEGRAM_BOT_TOKEN_TRADER}"
allowed_user_ids = [123456789]
resume_id = "${ENV:CODEX_CLI_RESUME_ID_TRADER}"
codex_workdir = "${ENV:CODEX_WORKDIR_TRADER}"
```

- [ ] **Step 4: Normalize and connect the ignored local configuration**

Mechanically rename the three `.env` keys from suffix `_Trader` to `_TRADER`
without displaying or changing their values. Add this ignored local block to
`config.toml`:

```toml
[[bots]]
name = "trader"
token = "${ENV:TELEGRAM_BOT_TOKEN_TRADER}"
allowed_user_ids = [938244537]
resume_id = "${ENV:CODEX_CLI_RESUME_ID_TRADER}"
codex_workdir = "${ENV:CODEX_WORKDIR_TRADER}"
```

- [ ] **Step 5: Validate the five-bot configuration and tests**

Run a redacted loader check that prints bot names, token presence and workdir
existence without printing token values, then run:

```bash
.venv/bin/pytest -q tests/test_config_loader.py
.venv/bin/pytest -q
```

Expected: the loader reports `stock`, `gateway`, `comfyUI`, `Measurement`, and
`trader`; all tests pass.

- [ ] **Step 6: Commit tracked trader support**

```bash
git add .env.example config.toml.example tests/test_config_loader.py
git commit -m "feat: document trader bot configuration"
```

Do not add `.env` or `config.toml` to Git.

### Task 4: Deploy and Verify Runtime Self-Healing

**Files:**
- Install from: `deploy/codex-session-gateway.service`
- Install to: `/etc/systemd/system/codex-session-gateway.service`

- [ ] **Step 1: Run pre-deployment verification**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/pip check
git diff --check
```

Expected: tests and dependency checks pass, with no whitespace errors.

- [ ] **Step 2: Install and reload the systemd unit**

```bash
sudo -n install -m 0644 deploy/codex-session-gateway.service /etc/systemd/system/codex-session-gateway.service
sudo -n systemctl daemon-reload
sudo -n systemctl start codex-session-gateway.service
```

Expected: the service enters `active (running)`.

- [ ] **Step 3: Verify five-bot startup**

Check `systemctl show`, the journal since the start timestamp, process threads,
Telegram connections and redacted `getMe` calls. Expected: all five bot IDs log
their polling startup, all five tokens pass `getMe`, and no startup traceback is
present.

- [ ] **Step 4: Exercise main-process self-healing**

Record the main PID, send it `SIGTERM`, and poll for up to 30 seconds until
systemd reports a different non-zero PID:

```bash
old_pid=$(systemctl show -p MainPID --value codex-session-gateway.service)
sudo -n kill -TERM "$old_pid"
```

Expected: after `RestartSec=5`, `MainPID` changes, the service is active again,
and all five bots restart.

- [ ] **Step 5: Verify final health**

Run the full tests once more and inspect the journal from the recovery timestamp.
Expected: tests pass; the service is active; the installed unit matches the
repository unit; all five bots are reachable; no unhandled startup, `RetryAfter`,
or polling conflict errors appear.

- [ ] **Step 6: Report repository state**

Show the new commits separately from the pre-existing uncommitted changes in
`src/adapters/telegram_adapter.py` and `tests/test_telegram_adapter.py`. Do not
commit or revert those pre-existing changes as part of this plan.
