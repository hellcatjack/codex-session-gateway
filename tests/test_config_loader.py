import textwrap

from src.config_loader import load_app_config, load_toml_config, resolve_env_placeholders


def test_env_placeholder_resolve():
    value = resolve_env_placeholders("${ENV:TELEGRAM_BOT_TOKEN_1}", {"TELEGRAM_BOT_TOKEN_1": "abc"})
    assert value == "abc"


def test_load_toml_config_ok(tmp_path):
    content = textwrap.dedent(
        """
        [base]
        db_path = "data/app.db"

        [[bots]]
        name = "bot-alpha"
        token = "${ENV:TELEGRAM_BOT_TOKEN_1}"
        allowed_user_ids = [1, 2]
        resume_id = "resume-1"
        codex_workdir = "/app/project-alpha"
        """
    ).strip()
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    result = load_toml_config(str(path), {"TELEGRAM_BOT_TOKEN_1": "abc"})
    assert not result.errors
    assert len(result.app_config.bots) == 1
    bot = result.app_config.bots[0]
    assert bot.token == "abc"
    assert bot.codex_workdir == "/app/project-alpha"

def test_load_toml_config_resume_id_defaults_to_auto(tmp_path):
    content = textwrap.dedent(
        """
        [base]
        db_path = "data/app.db"

        [[bots]]
        name = "bot-alpha"
        token = "token"
        allowed_user_ids = [1]
        codex_workdir = "/app/project-alpha"
        """
    ).strip()
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    result = load_toml_config(str(path), {})
    assert result.errors == []
    assert len(result.app_config.bots) == 1
    assert result.app_config.bots[0].resume_id == "auto"

def test_load_toml_config_resume_id_empty_defaults_to_auto(tmp_path):
    content = textwrap.dedent(
        """
        [base]
        db_path = "data/app.db"

        [[bots]]
        name = "bot-alpha"
        token = "token"
        allowed_user_ids = [1]
        resume_id = ""
        codex_workdir = "/app/project-alpha"
        """
    ).strip()
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    result = load_toml_config(str(path), {})
    assert result.errors == []
    assert len(result.app_config.bots) == 1
    assert result.app_config.bots[0].resume_id == "auto"

def test_load_app_config_env_defaults_resume_id_to_auto(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1")
    monkeypatch.delenv("CODEX_CLI_RESUME_ID", raising=False)
    monkeypatch.delenv("CODEX_WORKDIR", raising=False)
    app_config = load_app_config(path=str(tmp_path / "missing.toml"))
    assert len(app_config.bots) == 1
    bot = app_config.bots[0]
    assert bot.resume_id == "auto"
    assert bot.codex_workdir == str(tmp_path)


def test_missing_required_fields(tmp_path):
    content = textwrap.dedent(
        """
        [base]
        db_path = "data/app.db"

        [[bots]]
        name = "bot-alpha"
        token = "token"
        allowed_user_ids = [1]
        """
    ).strip()
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    result = load_toml_config(str(path), {})
    assert result.errors
    assert not result.app_config.bots

def test_base_codex_cli_args_empty_falls_back_to_env(tmp_path):
    toml = """
    [base]
    codex_cli_args = []

    [[bots]]
    name = "bot-alpha"
    token = "token"
    allowed_user_ids = [1]
    resume_id = "resume-1"
    codex_workdir = "/tmp"
    """
    path = tmp_path / "config.toml"
    path.write_text(toml, encoding="utf-8")

    result = load_toml_config(
        str(path),
        {"CODEX_CLI_ARGS": "--dangerously-bypass-approvals-and-sandbox"},
    )
    assert result.errors == []
    assert result.app_config.base.codex_cli_args == [
        "--dangerously-bypass-approvals-and-sandbox"
    ]


def test_base_jsonl_settings_support_codex_prefixed_env(tmp_path):
    toml = """
    [base]

    [[bots]]
    name = "bot-alpha"
    token = "token"
    allowed_user_ids = [1]
    resume_id = "resume-1"
    codex_workdir = "/tmp"
    """
    path = tmp_path / "config.toml"
    path.write_text(toml, encoding="utf-8")

    result = load_toml_config(
        str(path),
        {
            "CODEX_JSONL_STREAM_EVENTS": "0",
            "CODEX_JSONL_REASONING_THROTTLE_SECONDS": "1.5",
            "CODEX_JSONL_REASONING_MODE": "hidden",
        },
    )
    assert result.errors == []
    assert result.app_config.base.jsonl_stream_events is False
    assert result.app_config.base.jsonl_reasoning_throttle_seconds == 1.5
    assert result.app_config.base.jsonl_reasoning_mode == "hidden"


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
