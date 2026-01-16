import os
import re
import shlex
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for Python 3.10
    import tomli as tomllib
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .config import AppConfig, BaseConfig, BotConfig

_ENV_PATTERN = re.compile(r"\$\{ENV:([A-Z0-9_]+)\}")


@dataclass(frozen=True)
class ConfigLoadResult:
    app_config: AppConfig
    errors: list[str]


def _parse_int_set(value: str) -> set[int]:
    if not value.strip():
        return set()
    items = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    return set(items)


def _parse_optional(value: str) -> Optional[str]:
    text = value.strip()
    return text or None


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


def resolve_env_placeholders(value: str, env: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in env:
            raise KeyError(key)
        return env[key]

    return _ENV_PATTERN.sub(replace, value)


def _resolve_value(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        try:
            return resolve_env_placeholders(value, env)
        except KeyError as exc:
            raise ValueError(f"缺少环境变量 {exc.args[0]}") from exc
    return value


def _build_base_config(env: Mapping[str, str], base_data: Mapping[str, Any]) -> BaseConfig:
    def get(key: str, default: str) -> str:
        raw = base_data.get(key, env.get(key.upper(), default))
        raw = _resolve_value(raw, env)
        return str(raw)

    def get_bool(key: str, default: str) -> bool:
        raw = base_data.get(key, env.get(key.upper(), default))
        raw = _resolve_value(raw, env)
        return _parse_bool(str(raw))

    def get_float(key: str, default: str) -> float:
        raw = base_data.get(key, env.get(key.upper(), default))
        raw = _resolve_value(raw, env)
        return float(raw)

    def get_int(key: str, default: str) -> int:
        raw = base_data.get(key, env.get(key.upper(), default))
        raw = _resolve_value(raw, env)
        return int(raw)

    db_path = get("db_path", os.path.join("data", "app.db"))
    lock_path = base_data.get("lock_path") or env.get(
        "LOCK_PATH", os.path.join(os.path.dirname(db_path), "app.lock")
    )
    lock_path = _resolve_value(lock_path, env)

    codex_cli_args_value = base_data.get("codex_cli_args", env.get("CODEX_CLI_ARGS", ""))
    codex_cli_args_value = _resolve_value(codex_cli_args_value, env)
    if isinstance(codex_cli_args_value, list):
        codex_cli_args = [str(item) for item in codex_cli_args_value]
    else:
        codex_cli_args = shlex.split(str(codex_cli_args_value))

    return BaseConfig(
        db_path=db_path,
        lock_path=str(lock_path),
        codex_cli_cmd=get("codex_cli_cmd", "codex"),
        codex_cli_args=codex_cli_args,
        codex_cli_input_mode=get("codex_cli_input_mode", "stdin"),
        codex_cli_approvals_mode=_parse_optional(
            str(base_data.get("codex_cli_approvals_mode", env.get("CODEX_CLI_APPROVALS_MODE", "3")))
        ),
        codex_cli_skip_git_check=get_bool("codex_cli_skip_git_check", "1"),
        codex_cli_use_pty=get_bool("codex_cli_use_pty", "0"),
        stream_flush_interval=get_float("stream_flush_interval", "1.5"),
        stream_include_stderr=get_bool("stream_include_stderr", "0"),
        progress_tick_interval=get_float("progress_tick_interval", "15"),
        run_timeout_seconds=get_float("run_timeout_seconds", "900"),
        context_compaction_idle_timeout_seconds=get_float(
            "context_compaction_idle_timeout_seconds", "60"
        ),
        no_output_idle_timeout_seconds=get_float("no_output_idle_timeout_seconds", "900"),
        final_result_idle_timeout_seconds=get_float(
            "final_result_idle_timeout_seconds", "30"
        ),
        jsonl_sync_interval_seconds=get_float("jsonl_sync_interval_seconds", "3"),
        jsonl_stream_events=get_bool("jsonl_stream_events", "1"),
        jsonl_reasoning_throttle_seconds=get_float("jsonl_reasoning_throttle_seconds", "10"),
        jsonl_reasoning_mode=get("jsonl_reasoning_mode", "hidden"),
        message_chunk_limit=get_int("message_chunk_limit", "3500"),
    )


def _parse_allowed_user_ids(raw: Any, env: Mapping[str, str]) -> set[int]:
    value = _resolve_value(raw, env)
    if isinstance(value, list):
        return {int(item) for item in value}
    if isinstance(value, str):
        return _parse_int_set(value)
    return set()


def load_toml_config(path: str, env: Mapping[str, str] | None = None) -> ConfigLoadResult:
    if env is None:
        env = os.environ
    with open(path, "rb") as handle:
        data = tomllib.load(handle)

    base_data = data.get("base", {}) or {}
    bots_data = data.get("bots", []) or []

    base = _build_base_config(env, base_data)
    errors: list[str] = []
    bots: list[BotConfig] = []

    for idx, raw_bot in enumerate(bots_data):
        if not isinstance(raw_bot, dict):
            errors.append(f"bots[{idx}] 配置格式错误")
            continue
        try:
            name = str(_resolve_value(raw_bot.get("name", ""), env)).strip()
            token = str(_resolve_value(raw_bot.get("token", ""), env)).strip()
            resume_id = raw_bot.get("resume_id")
            resume_id = (
                _parse_optional(str(_resolve_value(resume_id, env))) if resume_id is not None else None
            )
            codex_workdir = str(_resolve_value(raw_bot.get("codex_workdir", ""), env)).strip()
            allowed_user_ids = _parse_allowed_user_ids(raw_bot.get("allowed_user_ids", ""), env)
        except ValueError as exc:
            errors.append(f"bots[{idx}] {exc}")
            continue

        missing = []
        if not name:
            missing.append("name")
        if not token:
            missing.append("token")
        if not allowed_user_ids:
            missing.append("allowed_user_ids")
        if not resume_id:
            missing.append("resume_id")
        if not codex_workdir:
            missing.append("codex_workdir")
        if missing:
            errors.append(f"bots[{idx}] 缺少字段: {', '.join(missing)}")
            continue

        codex_cli_args_raw = raw_bot.get("codex_cli_args")
        if codex_cli_args_raw is None:
            codex_cli_args = None
        else:
            codex_cli_args_raw = _resolve_value(codex_cli_args_raw, env)
            if isinstance(codex_cli_args_raw, list):
                codex_cli_args = [str(item) for item in codex_cli_args_raw]
            else:
                codex_cli_args = shlex.split(str(codex_cli_args_raw))

        bots.append(
            BotConfig(
                name=name,
                token=token,
                allowed_user_ids=allowed_user_ids,
                resume_id=resume_id,
                codex_workdir=codex_workdir,
                codex_cli_args=codex_cli_args,
            )
        )

    return ConfigLoadResult(app_config=AppConfig(base=base, bots=bots), errors=errors)


def load_app_config(path: str = "config.toml") -> AppConfig:
    _load_dotenv()
    env = os.environ
    if os.path.exists(path):
        result = load_toml_config(path, env)
        if not result.app_config.bots:
            raise RuntimeError("config.toml 未配置可用的 bot")
        if result.errors:
            # Keep running but print errors to stderr for visibility
            for err in result.errors:
                print(f"配置警告: {err}")
        return result.app_config

    # Fallback to legacy .env single-bot config
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_user_ids = _parse_int_set(env.get("TELEGRAM_ALLOWED_USER_IDS", ""))
    resume_id = _parse_optional(env.get("CODEX_CLI_RESUME_ID", ""))
    codex_workdir = env.get("CODEX_WORKDIR", os.getcwd())
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 未配置")
    if not allowed_user_ids:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS 未配置")
    if not resume_id:
        raise RuntimeError("CODEX_CLI_RESUME_ID 未配置")

    base = _build_base_config(env, {})
    bot = BotConfig(
        name="default",
        token=token,
        allowed_user_ids=allowed_user_ids,
        resume_id=resume_id,
        codex_workdir=codex_workdir,
    )
    return AppConfig(base=base, bots=[bot])
