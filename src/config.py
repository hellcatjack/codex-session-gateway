import os
import shlex
from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_allowed_user_ids: Set[int]
    codex_cli_cmd: str
    codex_cli_args: list[str]
    codex_cli_input_mode: str
    codex_cli_resume_id: Optional[str]
    codex_cli_approvals_mode: Optional[str]
    codex_cli_skip_git_check: bool
    codex_cli_use_pty: bool
    codex_workdir: str
    stream_flush_interval: float
    stream_include_stderr: bool
    progress_tick_interval: float
    run_timeout_seconds: float
    context_compaction_idle_timeout_seconds: float
    no_output_idle_timeout_seconds: float
    final_result_idle_timeout_seconds: float
    jsonl_sync_interval_seconds: float
    jsonl_stream_events: bool
    jsonl_reasoning_throttle_seconds: float
    jsonl_reasoning_mode: str
    message_chunk_limit: int


def _parse_int_set(value: str) -> Set[int]:
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
            if value and value[0] in ("'", "\"") and value[-1] == value[0]:
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


def load_config() -> Config:
    _load_dotenv()
    return Config(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_user_ids=_parse_int_set(
            os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        ),
        codex_cli_cmd=os.getenv("CODEX_CLI_CMD", "codex"),
        codex_cli_args=shlex.split(os.getenv("CODEX_CLI_ARGS", "")),
        codex_cli_input_mode=os.getenv("CODEX_CLI_INPUT_MODE", "stdin"),
        codex_cli_resume_id=_parse_optional(os.getenv("CODEX_CLI_RESUME_ID", "")),
        codex_cli_approvals_mode=_parse_optional(
            os.getenv("CODEX_CLI_APPROVALS_MODE", "3")
        ),
        codex_cli_skip_git_check=_parse_bool(
            os.getenv("CODEX_CLI_SKIP_GIT_CHECK", "1")
        ),
        codex_cli_use_pty=_parse_bool(os.getenv("CODEX_CLI_USE_PTY", "0")),
        codex_workdir=os.getenv("CODEX_WORKDIR", os.getcwd()),
        stream_flush_interval=float(os.getenv("STREAM_FLUSH_INTERVAL", "1.5")),
        stream_include_stderr=_parse_bool(os.getenv("STREAM_INCLUDE_STDERR", "0")),
        progress_tick_interval=float(os.getenv("PROGRESS_TICK_INTERVAL", "15")),
        run_timeout_seconds=float(os.getenv("RUN_TIMEOUT_SECONDS", "900")),
        context_compaction_idle_timeout_seconds=float(
            os.getenv("CONTEXT_COMPACTION_IDLE_TIMEOUT_SECONDS", "60")
        ),
        no_output_idle_timeout_seconds=float(
            os.getenv("NO_OUTPUT_IDLE_TIMEOUT_SECONDS", "900")
        ),
        final_result_idle_timeout_seconds=float(
            os.getenv("FINAL_RESULT_IDLE_TIMEOUT_SECONDS", "30")
        ),
        jsonl_sync_interval_seconds=float(
            os.getenv("JSONL_SYNC_INTERVAL_SECONDS", "3")
        ),
        jsonl_stream_events=_parse_bool(os.getenv("CODEX_JSONL_STREAM_EVENTS", "1")),
        jsonl_reasoning_throttle_seconds=float(
            os.getenv("CODEX_JSONL_REASONING_THROTTLE_SECONDS", "10")
        ),
        jsonl_reasoning_mode=os.getenv("CODEX_JSONL_REASONING_MODE", "hidden"),
        message_chunk_limit=int(os.getenv("MESSAGE_CHUNK_LIMIT", "3500")),
    )
