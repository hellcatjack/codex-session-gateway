import textwrap

from src.config_loader import load_toml_config, resolve_env_placeholders


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
