import shlex
from dataclasses import dataclass
from typing import Optional, Set, List


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


@dataclass(frozen=True)
class BaseConfig:
    db_path: str
    lock_path: str
    codex_cli_cmd: str
    codex_cli_args: List[str]
    codex_cli_input_mode: str
    codex_cli_approvals_mode: Optional[str]
    codex_cli_skip_git_check: bool
    codex_cli_use_pty: bool
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


@dataclass(frozen=True)
class BotConfig:
    name: str
    token: str
    allowed_user_ids: Set[int]
    resume_id: Optional[str]
    codex_workdir: str
    codex_cli_args: Optional[List[str]] = None


@dataclass(frozen=True)
class AppConfig:
    base: BaseConfig
    bots: List[BotConfig]


def build_runtime_config(base: BaseConfig, bot: BotConfig) -> Config:
    codex_cli_args = bot.codex_cli_args if bot.codex_cli_args is not None else base.codex_cli_args
    return Config(
        telegram_bot_token=bot.token,
        telegram_allowed_user_ids=bot.allowed_user_ids,
        codex_cli_cmd=base.codex_cli_cmd,
        codex_cli_args=codex_cli_args,
        codex_cli_input_mode=base.codex_cli_input_mode,
        codex_cli_resume_id=bot.resume_id,
        codex_cli_approvals_mode=base.codex_cli_approvals_mode,
        codex_cli_skip_git_check=base.codex_cli_skip_git_check,
        codex_cli_use_pty=base.codex_cli_use_pty,
        codex_workdir=bot.codex_workdir,
        stream_flush_interval=base.stream_flush_interval,
        stream_include_stderr=base.stream_include_stderr,
        progress_tick_interval=base.progress_tick_interval,
        run_timeout_seconds=base.run_timeout_seconds,
        context_compaction_idle_timeout_seconds=base.context_compaction_idle_timeout_seconds,
        no_output_idle_timeout_seconds=base.no_output_idle_timeout_seconds,
        final_result_idle_timeout_seconds=base.final_result_idle_timeout_seconds,
        jsonl_sync_interval_seconds=base.jsonl_sync_interval_seconds,
        jsonl_stream_events=base.jsonl_stream_events,
        jsonl_reasoning_throttle_seconds=base.jsonl_reasoning_throttle_seconds,
        jsonl_reasoning_mode=base.jsonl_reasoning_mode,
        message_chunk_limit=base.message_chunk_limit,
    )
